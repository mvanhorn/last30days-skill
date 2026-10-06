import json
import os
import socket
import signal
import sys
import threading
import time
from types import SimpleNamespace
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlsplit
from unittest.mock import patch

import pytest

from lib import bounded_get, doctor, health, http, reddit, subproc, usage


@pytest.fixture
def children(monkeypatch):
    processes = []
    popen = subproc.subprocess.Popen

    def start(*args, **kwargs):
        process = popen(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(subproc.subprocess, "Popen", start)
    yield processes
    assert processes
    assert all(process.poll() is not None for process in processes)
    assert all(process.pid not in subproc._child_pids for process in processes)


@pytest.fixture
def local_http():
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            parsed = urlsplit(self.path)
            query = parse_qs(parsed.query)
            if parsed.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", query["to"][0])
                self.end_headers()
                return
            status = int(parsed.path.rsplit("/", 1)[1]) if parsed.path.startswith("/status/") else 200
            if parsed.path == "/held-error":
                status = 403
            if query.get("url") == ["failed"]:
                status = 402
            self.send_response(status)
            self.send_header("Retry-After", "0")
            self.end_headers()
            hold = parsed.path in ("/hold", "/held-error") or query.get("url") == ["slow"]
            if hold:
                started.set()
                try:
                    while not release.wait(0.02):
                        self.wfile.write(b" ")
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    finished.set()
                return
            if parsed.path == "/echo":
                self.wfile.write(json.dumps(dict(self.headers)).encode())
                return
            if status != 200:
                self.wfile.write(b'{"error":"credits exhausted"}')
                return
            self.wfile.write(json.dumps({"comments": [{
                "body": "The fast comment survives.", "score": 17, "author": "alice",
            }]}).encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", started, release, finished
    finally:
        release.set()
        if started.is_set():
            assert finished.wait(2), "fixture request did not finish"
        server.shutdown()
        server.server_close()
        thread.join(2)
        assert not thread.is_alive()


def test_doctor_returns_before_held_response_is_released(local_http, children):
    url, started, release, _ = local_http
    result = {}
    returned = threading.Event()

    def run():
        result.update(doctor._probe_sources({}, timeout=0.8))
        returned.set()

    with patch.object(doctor, "_probeable_sources", return_value=("reddit",)), \
         patch.dict(doctor._HTTP_PROBE_URLS, {"reddit": f"{url}/hold"}):
        caller = threading.Thread(target=run)
        caller.start()
        try:
            assert started.wait(2), "real HTTP body read was not reached"
            assert any(process.poll() is None for process in children)
            assert returned.wait(1.0), "doctor waited for the stalled response"
            assert all(process.poll() is not None for process in children), "owned HTTP worker survived timeout"
            assert result["reddit"] == {
                "ok": False, "detail": "probe exceeded deadline", "probed": True,
            }
        finally:
            release.set()
            caller.join(3)
            assert not caller.is_alive()


def test_reddit_keeps_fast_comments_and_reaps_stalled_transport(local_http, children, monkeypatch):
    url, started, release, _ = local_http
    monkeypatch.setattr(reddit, "SCRAPECREATORS_BASE", url)
    items = [
        {"id": "fast", "url": "fast", "engagement": {"score": 10}},
        {"id": "slow", "url": "slow", "engagement": {"score": 20}},
    ]
    returned = threading.Event()
    result = []

    def run():
        result.extend(reddit.enrich_with_comments(items, "dummy-key", depth="quick", budget_seconds=1.2))
        returned.set()

    caller = threading.Thread(target=run)
    caller.start()
    try:
        assert started.wait(2), "real HTTP body read was not reached"
        assert returned.wait(1.8), "Reddit waited for the stalled response"
        assert len(result) == 2
        assert result[0]["top_comments"][0]["excerpt"] == "The fast comment survives."
        assert result[0]["top_comments"][0]["score"] == 17
        assert "top_comments" not in result[1]
        assert all(process.poll() is not None for process in children)
        before_release = json.dumps(result, sort_keys=True)
    finally:
        release.set()
        caller.join(3)
        assert not caller.is_alive()
    assert json.dumps(result, sort_keys=True) == before_release


@pytest.mark.parametrize("path,body_check,expected", [
    ("/hold", None, True),
    ("/held-error", lambda body: "must not run", True),
])
def test_doctor_header_only_modes_do_not_read_bodies(local_http, children, path, body_check, expected):
    url, _, _, _ = local_http
    result = doctor._http_ok(f"{url}{path}", 2, body_check=body_check, deadline_monotonic=time.monotonic() + 2)
    assert result == (expected, "HTTP 403" if path == "/held-error" else "HTTP 200")


@pytest.mark.parametrize("status,state", [(401, health.AUTH_FAILED), (402, health.PAYMENT_REQUIRED), (429, health.RATE_LIMITED)])
def test_owned_get_preserves_error_body_and_failure_capture(local_http, children, status, state):
    url, _, _, _ = local_http
    with http.capture_failures() as failures:
        with pytest.raises(http.HTTPError) as caught:
            http.get(f"{url}/status/{status}", retries=1, max_429_retries=1,
                     deadline_monotonic=time.monotonic() + 2, owned_get=True)
    assert caught.value.status_code == status
    assert caught.value.body == '{"error":"credits exhausted"}'
    assert caught.value.outcome_state == state
    assert failures == [caught.value]


@pytest.mark.parametrize("cross_origin", [False, True])
def test_owned_get_preserves_redirect_auth_policy(local_http, children, cross_origin):
    url, _, _, _ = local_http
    target = url.replace("127.0.0.1", "localhost") if cross_origin else url
    endpoint = f"{url}/redirect?{urlencode({'to': target + '/echo'})}"
    result = http.get(endpoint, headers={"Authorization": "Bearer dummy", "x-api-key": "dummy-key"},
                      deadline_monotonic=time.monotonic() + 3, owned_get=True)
    lowered = {key.lower(): value for key, value in result.items()}
    if cross_origin:
        assert "authorization" not in lowered
        assert "x-api-key" not in lowered
    else:
        assert lowered["authorization"] == "Bearer dummy"
        assert lowered["x-api-key"] == "dummy-key"


def test_owned_get_cancellation_reaps_transport(local_http, children):
    url, started, release, _ = local_http
    cancel = threading.Event()
    returned = threading.Event()
    caught = []

    def run():
        try:
            bounded_get.get(urllib.request.Request(f"{url}/hold"), timeout=5,
                            deadline_monotonic=time.monotonic() + 5, cancel=cancel)
        except bounded_get.GetTimeout as exc:
            caught.append(exc)
        finally:
            returned.set()

    caller = threading.Thread(target=run)
    caller.start()
    try:
        assert started.wait(2)
        cancel.set()
        assert returned.wait(1)
        assert len(caught) == 1
        assert all(process.poll() is not None for process in children)
    finally:
        release.set()
        caller.join(3)
        assert not caller.is_alive()


def test_stalled_error_body_is_reaped_and_captured_as_timeout(local_http, children):
    url, started, release, _ = local_http
    with http.capture_failures() as failures:
        try:
            with pytest.raises(http.DeadlineExceeded) as caught:
                http.get(f"{url}/held-error", retries=1,
                         deadline_monotonic=time.monotonic() + 0.8, owned_get=True)
            assert started.is_set(), "real HTTP error body read was not reached"
            assert caught.value.outcome_state == health.TIMEOUT
            assert failures == [caught.value]
            assert all(process.poll() is not None for process in children)
        finally:
            release.set()


def test_reddit_comment_worker_preserves_parent_failure_capture(local_http, children, monkeypatch):
    url, _, _, _ = local_http
    monkeypatch.setattr(reddit, "SCRAPECREATORS_BASE", url)
    items = [{"id": "failed", "url": "failed", "engagement": {"score": 10}}]
    with http.capture_failures() as failures:
        result = reddit.enrich_with_comments(items, "dummy-key", budget_seconds=2)
    assert result is items
    assert "top_comments" not in result[0]
    assert len(failures) == 1
    assert failures[0].status_code == 402
    assert failures[0].outcome_state == health.PAYMENT_REQUIRED
    assert failures[0].body == '{"error":"credits exhausted"}'


def test_dns_error_wire_record_retains_retry_signal():
    restored = bounded_get._error_from_record({
        "kind": "URLError", "reason": {"kind": "gaierror", "errno": socket.EAI_AGAIN,
                                         "message": "temporary DNS failure"},
    })
    assert isinstance(restored, urllib.error.URLError)
    assert isinstance(restored.reason, socket.gaierror)
    assert restored.reason.errno == socket.EAI_AGAIN
    assert http._is_dns_failure(restored)


def test_owned_get_replay_skips_child_and_request_accounting(local_http, children, tmp_path):
    url, _, _, _ = local_http
    fixture = tmp_path / "fixture"
    with http.recording_requests(fixture):
        recorded = http.get(f"{url}/echo", deadline_monotonic=time.monotonic() + 2, owned_get=True)
    with patch.object(bounded_get, "get", side_effect=AssertionError("replay launched transport")), \
         patch.object(usage, "begin_http") as begin, http.replaying_requests(fixture):
        replayed = http.get(f"{url}/echo", deadline_monotonic=time.monotonic() + 2, owned_get=True)
    assert replayed == recorded
    begin.assert_not_called()


def test_unstarted_owned_get_cancels_request_charge(local_http):
    url, _, _, _ = local_http
    charge = ("scrapecreators", "request", "dummy-attempt")
    with patch.object(usage, "begin_http", return_value=charge) as begin, \
         patch.object(usage, "cancel") as cancel, patch.object(subproc.subprocess, "Popen") as spawn:
        with pytest.raises(http.DeadlineExceeded):
            http.get(f"{url}/echo", deadline_monotonic=time.monotonic() + 0.1, owned_get=True)
    begin.assert_called_once()
    cancel.assert_called_once_with(charge)
    spawn.assert_not_called()


@pytest.mark.parametrize("kwargs", [
    {"method": "POST", "deadline_monotonic": 1},
    {"method": "GET", "json_data": {"key": "value"}, "deadline_monotonic": 1},
    {"method": "GET"},
])
def test_owned_transport_rejects_unsupported_calls_before_accounting(kwargs):
    with patch.object(usage, "begin_http") as begin, patch.object(bounded_get, "get") as transport:
        with pytest.raises(ValueError):
            http.request(url="http://example.test", owned_get=True, **kwargs)
    begin.assert_not_called()
    transport.assert_not_called()


def test_subprocess_without_process_groups_still_reaps_child(children, monkeypatch):
    monkeypatch.setattr(subproc, "os", SimpleNamespace(kill=os.kill))
    with pytest.raises(subproc.SubprocTimeout):
        subproc.run_with_timeout([sys.executable, "-c", "import time; time.sleep(30)"],
                                 timeout=0.05, cleanup_grace=0.1)
    assert all(process.poll() is not None for process in children)


def test_unexpected_parent_failure_reaps_owned_child(children, monkeypatch):
    start = subproc.subprocess.Popen

    def fail_after_start(*args, **kwargs):
        process = start(*args, **kwargs)
        def fail(**kwargs):
            raise RuntimeError("parent communication failed")
        process.communicate = fail
        return process

    monkeypatch.setattr(subproc.subprocess, "Popen", fail_after_start)
    with pytest.raises(RuntimeError, match="parent communication failed"):
        subproc.run_with_timeout([sys.executable, "-c", "import time; time.sleep(30)"],
                                 timeout=2, cleanup_grace=0.1)
    assert all(process.poll() is not None for process in children)


@pytest.mark.skipif(not hasattr(os, "killpg"), reason="POSIX process-group termination")
def test_health_timeout_terminates_inherited_output_descendant(tmp_path, children, monkeypatch):
    descendant_pid = tmp_path / "descendant.pid"
    stopped = tmp_path / "descendant.stopped"
    child = (
        "import os, signal, sys, time\nfrom pathlib import Path\n"
        f"def stop(*args):\n Path({str(stopped)!r}).write_text('terminated')\n sys.exit(0)\n"
        "signal.signal(signal.SIGTERM, stop)\n"
        f"Path({str(descendant_pid)!r}).write_text(str(os.getpid()))\n"
        "time.sleep(30)\n"
    )
    command = tmp_path / "version.py"
    command.write_text(f"import subprocess, sys, time\nsubprocess.Popen([sys.executable, '-c', {child!r}])\ntime.sleep(30)\n")
    monkeypatch.setitem(health._VERSION_ARGS, sys.executable, [str(command)])
    started = time.monotonic()
    try:
        result = health._probe_dependency_uncached(sys.executable, 0.8)
        assert time.monotonic() - started < 1.5
        assert result.status == health.TIMEOUT
        assert descendant_pid.exists(), "real descendant was not reached"
        assert stopped.exists(), "timed-out version probe left its descendant running"
        assert stopped.read_text() == "terminated"
        assert all(process.poll() is not None for process in children)
    finally:
        if descendant_pid.exists() and not stopped.exists():
            try:
                os.kill(int(descendant_pid.read_text()), signal.SIGTERM)
            except ProcessLookupError:
                pass


def test_health_real_version_command_preserves_first_line(children, monkeypatch):
    monkeypatch.setitem(health._VERSION_ARGS, sys.executable, [
        "-c", "import sys; print('1.2.3'); print('ignored second line'); print('stderr detail', file=sys.stderr)",
    ])
    result = health._probe_dependency_uncached(sys.executable, 2)
    assert result.status == health.OK
    assert result.detail == "1.2.3"
    assert result.prescription == ""
    assert all(process.stdout is None and process.stderr is None for process in children)


def test_file_capture_returns_bounded_stdout_and_stderr(children):
    result = subproc.run_with_timeout([
        sys.executable, "-c", "import sys; print('a' * 100); print('b' * 100, file=sys.stderr)",
    ], timeout=2, capture_limit_bytes=16)
    assert result.returncode == 0
    assert result.stdout == "a" * 16
    assert result.stderr == "b" * 16
    assert all(process.stdout is None and process.stderr is None for process in children)


def test_file_capture_without_process_groups_avoids_descendant_pipe_wait(tmp_path, children, monkeypatch):
    descendant_pid = tmp_path / "descendant.pid"
    command = tmp_path / "version.py"
    child = "import time; time.sleep(30)"
    command.write_text(
        "import subprocess, sys, time\nfrom pathlib import Path\n"
        f"child = subprocess.Popen([sys.executable, '-c', {child!r}])\n"
        f"Path({str(descendant_pid)!r}).write_text(str(child.pid))\n"
        "time.sleep(30)\n"
    )
    monkeypatch.setattr(subproc, "os", SimpleNamespace(kill=os.kill))
    started = time.monotonic()
    try:
        with pytest.raises(subproc.SubprocTimeout):
            subproc.run_with_timeout([sys.executable, str(command)], timeout=0.6,
                                     cleanup_grace=0.1, capture_limit_bytes=64 * 1024)
        assert time.monotonic() - started < 1.5
        assert descendant_pid.exists(), "real descendant was not reached"
        assert all(process.poll() is not None for process in children)
        assert all(process.stdout is None and process.stderr is None for process in children)
    finally:
        if descendant_pid.exists():
            try:
                os.kill(int(descendant_pid.read_text()), signal.SIGTERM)
            except ProcessLookupError:
                pass


def test_doctor_retry_spends_one_operation_budget(monkeypatch):
    clock = [100.0]
    timeouts = []

    def get(*args, **kwargs):
        timeouts.append((args[1], kwargs["deadline_monotonic"]))
        clock[0] += 6 if len(timeouts) == 1 else 0.5
        return (False, "HTTP 429") if len(timeouts) == 1 else (True, "HTTP 200")

    monkeypatch.setattr(doctor.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(doctor.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    monkeypatch.setattr(doctor, "_http_ok", get)
    assert doctor._probe_source("reddit", {}, 10)["ok"]
    assert timeouts == [(10, 110), (2, 110)]


def test_doctor_does_not_retry_when_backoff_exceeds_budget(monkeypatch):
    clock = [100.0]

    def get(*args, **kwargs):
        clock[0] += 9
        return False, "HTTP 429"

    monkeypatch.setattr(doctor.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(doctor, "_http_ok", get)
    with patch.object(doctor.time, "sleep") as sleep:
        result = doctor._probe_source("reddit", {}, 10)
    assert result["detail"] == "probe exceeded deadline"
    sleep.assert_not_called()


def test_doctor_cli_receives_remaining_timeout(monkeypatch):
    clock = iter([100.0, 100.25, 100.5])
    monkeypatch.setattr(doctor.time, "monotonic", lambda: next(clock))
    probe = health.DependencyProbe(name="yt-dlp", status=health.OK, detail="version")
    with patch.object(health, "probe_dependency", return_value=probe) as dependency:
        assert doctor._probe_source("youtube", {}, 5)["ok"]
    dependency.assert_called_once_with("yt-dlp", timeout=4.75)


def test_queued_doctor_probe_gets_its_own_start_deadline(monkeypatch):
    clock = [100.0]
    deadlines = []

    def get(*args, **kwargs):
        deadlines.append(kwargs["deadline_monotonic"])
        clock[0] += 8
        return True, "HTTP 200"

    monkeypatch.setattr(doctor.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(doctor, "_http_ok", get)
    monkeypatch.setattr(doctor, "_probeable_sources", lambda: ("github", "hackernews"))
    monkeypatch.setattr(doctor.concurrent.futures, "ThreadPoolExecutor", lambda **kwargs: ThreadPoolExecutor(max_workers=1))
    assert all(result["ok"] for result in doctor._probe_sources({}, 10).values())
    assert deadlines == [110, 118]
