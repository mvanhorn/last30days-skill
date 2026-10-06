import os
import socket
import subprocess
import sys
import textwrap
import uuid
from pathlib import Path

import pytest


def _run_probe(tmp_path, body):
    probe = tmp_path / "test_probe.py"
    probe.write_text(textwrap.dedent(body), encoding="utf-8")
    root = Path(__file__).resolve().parents[1]
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join([str(root), environment.get("PYTHONPATH", "")])
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--tb=short", "-p", "tests.network_guard", str(probe)],
        cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=30,
    )


@pytest.mark.parametrize("operation", [
    "socket.create_connection((host, 443))",
    "socket.getaddrinfo(host, 443)",
    "socket.gethostbyname(host)",
    "socket.gethostbyname_ex(host)",
    "socket.gethostbyaddr(host)",
    "socket.socket().connect((host, 443))",
    "socket.socket().connect_ex((host, 443))",
    "socket.socket(socket.AF_INET, socket.SOCK_DGRAM).sendto(b'probe', (host, 53))",
])
def test_swallowed_network_attempt_fails_test(tmp_path, operation):
    host = f"forbidden-{uuid.uuid4().hex}.invalid"
    result = _run_probe(tmp_path, f"""
        import socket

        def test_caught_transport_error():
            host = {host!r}
            try:
                {operation}
            except OSError:
                pass
    """)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "1 passed, 1 error" in result.stdout
    assert "Unexpected network attempts" in result.stdout
    assert host in result.stdout


@pytest.mark.parametrize("address", [("203.0.113.8", 443), ("127.0.0.1", 18800), ("localhost", 9222)])
def test_unowned_ip_or_loopback_connection_fails_test(tmp_path, address):
    result = _run_probe(tmp_path, f"""
        import socket

        def test_no_ambient_service():
            try:
                socket.create_connection({address!r})
            except OSError:
                pass
    """)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "1 passed, 1 error" in result.stdout
    assert "Unexpected network attempts" in result.stdout
    assert address[0] in result.stdout


def test_swallowed_worker_network_attempt_fails_test(tmp_path):
    host = f"worker-{uuid.uuid4().hex}.invalid"
    result = _run_probe(tmp_path, f"""
        import socket
        from concurrent.futures import ThreadPoolExecutor

        def test_worker():
            def work():
                try:
                    socket.create_connection(({host!r}, 443))
                except OSError:
                    return []
            with ThreadPoolExecutor(max_workers=1) as executor:
                assert executor.submit(work).result() == []
    """)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "1 passed, 1 error" in result.stdout
    assert "Unexpected network attempts" in result.stdout
    assert host in result.stdout


def test_owned_loopback_transport_remains_real(tmp_path):
    result = _run_probe(tmp_path, """
        import socket

        def test_local_exchange():
            with socket.socket() as server:
                server.bind(('127.0.0.1', 0))
                server.listen()
                with socket.create_connection(('localhost', server.getsockname()[1])) as client:
                    client.sendall(b'owned transport')
                    connection, _ = server.accept()
                    with connection:
                        assert connection.recv(64) == b'owned transport'
    """)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout


def test_owned_port_does_not_authorize_another_loopback_address(tmp_path):
    canary = f"address-{uuid.uuid4().hex}"
    result = _run_probe(tmp_path, f"""
        import socket
        import pytest

        ambient = socket.socket()
        ambient.bind(('127.0.0.2', 0))
        ambient.listen()
        ambient.setblocking(False)

        def test_exact_address():
            with ambient, socket.socket() as owned:
                owned.bind(('127.0.0.1', ambient.getsockname()[1]))
                try:
                    with socket.create_connection(ambient.getsockname()) as client:
                        client.sendall({canary.encode()!r})
                except OSError:
                    pass
                with pytest.raises(BlockingIOError):
                    accepted, _ = ambient.accept()
                    with accepted:
                        assert accepted.recv(128) != {canary.encode()!r}
    """)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "1 passed, 1 error" in result.stdout
    assert "Unexpected network attempts" in result.stdout
    assert "127.0.0.2" in result.stdout


@pytest.mark.parametrize("operation", ["sendto", "sendmsg"])
def test_owned_tcp_port_does_not_authorize_udp(tmp_path, operation):
    if operation == "sendmsg" and not hasattr(socket.socket, "sendmsg"):
        pytest.skip("sendmsg is unavailable on this platform")
    canary = f"protocol-{uuid.uuid4().hex}"
    call = f"sender.sendto({canary.encode()!r}, address)" if operation == "sendto" else f"sender.sendmsg([{canary.encode()!r}], [], 0, address)"
    result = _run_probe(tmp_path, f"""
        import socket
        import pytest

        ambient = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        ambient.bind(('127.0.0.1', 0))
        ambient.setblocking(False)

        def test_exact_transport():
            with ambient, socket.socket() as owned, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
                address = ambient.getsockname()
                owned.bind(address)
                try:
                    {call}
                except OSError:
                    pass
                with pytest.raises(BlockingIOError):
                    assert ambient.recv(128) != {canary.encode()!r}
    """)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "1 passed, 1 error" in result.stdout
    assert "Unexpected network attempts" in result.stdout
    assert operation in result.stdout


@pytest.mark.parametrize("operation", ["sendto", "sendmsg"])
def test_owned_udp_transport_remains_real(tmp_path, operation):
    if operation == "sendmsg" and not hasattr(socket.socket, "sendmsg"):
        pytest.skip("sendmsg is unavailable on this platform")
    canary = f"owned-{uuid.uuid4().hex}"
    call = f"sender.sendto({canary.encode()!r}, address)" if operation == "sendto" else f"sender.sendmsg([{canary.encode()!r}], [], 0, address)"
    result = _run_probe(tmp_path, f"""
        import socket

        def test_udp_exchange():
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as server, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
                server.bind(('127.0.0.1', 0))
                server.settimeout(1)
                address = ('localhost', server.getsockname()[1])
                {call}
                assert server.recv(128) == {canary.encode()!r}
    """)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout


def test_detached_fixture_does_not_authorize_a_port(tmp_path):
    result = _run_probe(tmp_path, """
        import os
        import socket

        def test_detached_fixture():
            server = socket.socket()
            server.bind(('127.0.0.1', 0))
            address = server.getsockname()
            descriptor = server.detach()
            os.close(descriptor)
            try:
                socket.create_connection(address)
            except OSError:
                pass
    """)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "1 passed, 1 error" in result.stdout
    assert "Unexpected network attempts" in result.stdout


def test_closed_fixture_does_not_authorize_a_port(tmp_path):
    result = _run_probe(tmp_path, """
        import socket

        def test_fixture_closed():
            with socket.socket() as server:
                server.bind(('127.0.0.1', 0))
                address = server.getsockname()
            try:
                socket.create_connection(address)
            except OSError:
                pass
    """)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "1 passed, 1 error" in result.stdout
    assert "Unexpected network attempts" in result.stdout


def test_fixture_teardown_network_attempt_fails_test(tmp_path):
    host = f"teardown-{uuid.uuid4().hex}.invalid"
    result = _run_probe(tmp_path, f"""
        import socket
        import pytest

        @pytest.fixture
        def resource():
            yield
            try:
                socket.create_connection(({host!r}, 443))
            except OSError:
                pass

        def test_teardown(resource):
            pass
    """)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "1 passed, 1 error" in result.stdout
    assert "Unexpected network attempts" in result.stdout
    assert host in result.stdout


@pytest.mark.parametrize("allowed", [False, True])
def test_worker_boundary_guards_destination_and_preserves_allowed_call(tmp_path, allowed):
    host = f"worker-boundary-{uuid.uuid4().hex}.invalid"
    result = _run_probe(tmp_path, f"""
        import socket
        import sys
        import types
        from urllib.request import Request

        package = types.ModuleType('lib')
        package.__path__ = []
        worker = types.ModuleType('lib.bounded_get')
        calls = []
        def get(req, **kwargs):
            calls.append((req.full_url, kwargs))
            return b'owned payload'
        worker.get = get
        sys.modules['lib'] = package
        sys.modules['lib.bounded_get'] = worker

        def test_worker_destination():
            if {allowed!r}:
                with socket.socket() as server:
                    server.bind(('127.0.0.1', 0))
                    url = f'http://127.0.0.1:{{server.getsockname()[1]}}/fixture'
                    assert worker.get(Request(url), timeout=1, deadline_monotonic=123) == b'owned payload'
                    assert calls == [(url, {{'timeout': 1, 'deadline_monotonic': 123}})]
            else:
                try:
                    worker.get(Request('https://{host}/fixture'), timeout=1, deadline_monotonic=123)
                except OSError:
                    pass
                assert calls == []
    """)
    assert result.returncode == (0 if allowed else 1), result.stdout + result.stderr
    if allowed:
        assert "1 passed" in result.stdout
    else:
        assert "1 passed, 1 error" in result.stdout
        assert "Unexpected network attempts" in result.stdout
        assert host in result.stdout
