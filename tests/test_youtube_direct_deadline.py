"""Direct transcript fallback must finish within the YouTube lane budget."""

import contextlib
import io
import json
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import pytest

from lib import bounded_get, subproc, youtube_yt


_VTT = "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nThe comparison has useful details.\n"


def _watch_page(captions=True):
    tracks = [{"languageCode": "en", "baseUrl": "https://www.youtube.com/captions"}] if captions else []
    player = {"captions": {"playerCaptionsTracklistRenderer": {"captionTracks": tracks}}}
    return f"<script>var ytInitialPlayerResponse = {json.dumps(player)};</script>"


def test_expired_ssh_transcript_fallback_starts_no_http(monkeypatch, tmp_path):
    requests = []
    launches = []

    def urlopen(req, **kwargs):
        requests.append(req.full_url)
        return io.BytesIO(_watch_page(captions=False).encode())

    def launch(*args, **kwargs):
        launches.append(args)
        return subproc.SubprocResult(returncode=1, stdout="", stderr="")

    monkeypatch.setenv("LAST30DAYS_YOUTUBE_SSH_HOST", "test-transcript-host")
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(subproc, "run_with_timeout", launch)
    deadline = time.monotonic() + youtube_yt._LANE_RETURN_MARGIN_SECONDS - 1

    with youtube_yt.sc_spend_until(lambda: False, deadline=deadline):
        result = youtube_yt.fetch_transcript("expired", str(tmp_path), {})

    assert result is None
    assert launches == [], "an expired lane launched an SSH subprocess"
    assert requests == [], "the expired SSH attempt started direct HTTP fallback"


@pytest.mark.parametrize("lane_bound", [False, True])
@pytest.mark.parametrize("captions", [False, True])
def test_direct_transcript_preserves_results_and_bounds_both_requests(monkeypatch, lane_bound, captions):
    plain_requests = []
    bounded_requests = []
    cancel = threading.Event()
    deadline = time.monotonic() + 30
    cutoff = deadline - youtube_yt._LANE_RETURN_MARGIN_SECONDS

    def body(req):
        return (_watch_page(captions) if urlsplit(req.full_url).path == "/watch" else _VTT).encode()

    def urlopen(req, **kwargs):
        plain_requests.append(req.full_url)
        return io.BytesIO(body(req))

    def bounded(req, **kwargs):
        bounded_requests.append((req.full_url, kwargs))
        return 200, body(req), None

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(bounded_get, "get", bounded)
    context = (
        youtube_yt.sc_spend_until(lambda: False, cancel=cancel, deadline=deadline)
        if lane_bound else contextlib.nullcontext()
    )
    status = {}
    with context:
        result = youtube_yt._fetch_transcript_direct("direct", status=status)

    assert result == (_VTT if captions else None)
    assert status == ({} if captions else {"no_caption_tracks": True})
    expected_paths = ["/watch", "/captions"] if captions else ["/watch"]
    if lane_bound:
        assert [urlsplit(url).path for url, _ in bounded_requests] == expected_paths
        assert plain_requests == [], "lane-bound transcript bypassed the bounded transport"
        for _, bounds in bounded_requests:
            assert bounds["deadline_monotonic"] == pytest.approx(cutoff, rel=0, abs=0.001)
            assert bounds["cancel"] is cancel
            assert bounds["read_error_body"] is False
    else:
        assert [urlsplit(url).path for url in plain_requests] == expected_paths
        assert bounded_requests == []


@pytest.mark.parametrize("held_path", ["/watch", "/captions"])
def test_direct_transcript_reaps_held_body_before_lane_cutoff(monkeypatch, held_path):
    started = threading.Event()
    release = threading.Event()
    returned = threading.Event()
    result = {}
    children = []
    original_popen = subproc.subprocess.Popen
    original_urlopen = urllib.request.urlopen
    original_bounded = bounded_get.get

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            path = urlsplit(self.path).path
            body = (_watch_page() if path == "/watch" else _VTT).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if path == held_path:
                started.set()
                release.wait(10)
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server_thread = threading.Thread(target=server.serve_forever)
    server_thread.start()

    def local_request(req):
        parsed = urlsplit(req.full_url)
        url = f"http://127.0.0.1:{server.server_port}{parsed.path}"
        if parsed.query:
            url += f"?{parsed.query}"
        return urllib.request.Request(url, headers=dict(req.header_items()))

    def urlopen(req, **kwargs):
        return original_urlopen(local_request(req), **kwargs)

    def bounded(req, **kwargs):
        return original_bounded(local_request(req), **kwargs)

    def popen(*args, **kwargs):
        process = original_popen(*args, **kwargs)
        children.append(process)
        return process

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(bounded_get, "get", bounded)
    monkeypatch.setattr(subproc.subprocess, "Popen", popen)
    monkeypatch.setattr(youtube_yt, "_LANE_RETURN_MARGIN_SECONDS", 1.0)
    deadline = time.monotonic() + 3
    cutoff = deadline - 1

    def fetch():
        try:
            with youtube_yt.sc_spend_until(lambda: False, deadline=deadline):
                result["transcript"] = youtube_yt._fetch_transcript_direct("held", timeout=10)
        except BaseException as error:
            result["error"] = error
        finally:
            returned.set()

    caller = threading.Thread(target=fetch)
    caller.start()
    try:
        assert started.wait(1), "the real HTTP body read was not reached"
        assert returned.wait(max(0, cutoff - time.monotonic()) + 0.3), "direct HTTP outlived the lane cutoff"
        assert result == {"transcript": None}
        assert children, "the deadline-bound request had no owned transport"
        assert all(child.poll() is not None for child in children), "an HTTP worker survived the lane cutoff"
        assert all(child.pid not in subproc._child_pids for child in children)
    finally:
        release.set()
        caller.join(3)
        server.shutdown()
        server.server_close()
        server_thread.join(3)
        assert not caller.is_alive()
        assert not server_thread.is_alive()
