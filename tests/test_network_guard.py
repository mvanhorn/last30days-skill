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


@pytest.mark.parametrize("operation, host, expected", [
    ("gethostbyname", "localhost", "127.0.0.1"),
    ("gethostbyname", "127.0.0.2", "127.0.0.2"),
    ("gethostbyname_ex", "localhost", ("localhost", [], ["127.0.0.1"])),
    ("gethostbyname_ex", "127.0.0.2", ("127.0.0.2", [], ["127.0.0.2"])),
    ("gethostbyaddr", "localhost", ("localhost", [], ["127.0.0.1"])),
    ("gethostbyaddr", "127.0.0.2", ("localhost", [], ["127.0.0.2"])),
    ("gethostbyaddr", "::1", ("localhost", [], ["::1"])),
])
def test_loopback_resolvers_do_not_delegate_to_libc(tmp_path, operation, host, expected):
    result = _run_probe(tmp_path, f"""
        import socket

        calls = []
        def system_resolver(host):
            calls.append(host)
            return {expected!r}
        socket.{operation} = system_resolver

        def test_local_answer():
            assert socket.{operation}({host!r}) == {expected!r}
            assert calls == [], 'loopback lookup reached the system resolver'
    """)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout


@pytest.mark.parametrize("operation", ["gethostbyname", "gethostbyname_ex"])
def test_ipv4_resolvers_reject_ipv6_without_libc(tmp_path, operation):
    result = _run_probe(tmp_path, f"""
        import socket
        import pytest

        calls = []
        def system_resolver(host):
            calls.append(host)
            raise socket.gaierror(socket.EAI_FAMILY, 'IPv4 lookup requires an IPv4 address')
        socket.{operation} = system_resolver

        def test_ipv4_only_contract():
            with pytest.raises(socket.gaierror):
                socket.{operation}('::1')
            assert calls == [], 'IPv6 input reached the IPv4 system resolver'
    """)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout


def test_owned_getaddrinfo_delegates_only_numeric_addresses_and_services(tmp_path):
    result = _run_probe(tmp_path, """
        import socket

        system_resolver = socket.getaddrinfo
        calls = []
        def numeric_resolver(host, port, family=0, type=0, proto=0, flags=0):
            calls.append((host, port, flags))
            assert flags & socket.AI_NUMERICHOST, 'system resolver lacks AI_NUMERICHOST'
            assert flags & socket.AI_NUMERICSERV, 'system resolver lacks AI_NUMERICSERV'
            return system_resolver(host, port, family, type, proto, flags)
        socket.getaddrinfo = numeric_resolver

        def test_owned_localhost():
            with socket.socket() as server:
                server.bind(('127.0.0.1', 0))
                port = server.getsockname()[1]
                result = socket.getaddrinfo('localhost', port, type=socket.SOCK_STREAM)
                assert {item[4] for item in result} == {('127.0.0.1', port)}
                assert calls
                assert all(host == '127.0.0.1' for host, _, _ in calls)
    """)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout


@pytest.mark.parametrize("form", ["text", "bytes", "four_byte_hostname"])
def test_bind_hostname_is_denied_before_original_method(tmp_path, form):
    host = f"bind-{uuid.uuid4().hex}.invalid"
    if form == "bytes":
        host = host.encode("ascii")
    elif form == "four_byte_hostname":
        host = b"abcd"
    result = _run_probe(tmp_path, f"""
        import socket

        delegated = []
        def original_bind(sock, address):
            delegated.append(address)
            raise OSError('controlled bind resolver canary; no DNS was called')
        socket.socket.bind = original_bind

        def test_denied_hostname():
            with socket.socket() as server:
                try:
                    server.bind(({host!r}, 0))
                except OSError:
                    pass
            assert delegated == [], 'hostname reached original bind'
    """)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "1 passed, 1 error" in result.stdout, result.stdout + result.stderr
    assert "Unexpected network attempts" in result.stdout
    assert "bind:" in result.stdout


@pytest.mark.parametrize("family, host, expected", [
    ("AF_INET", "localhost", "127.0.0.1"),
    ("AF_INET", "", "0.0.0.0"),
    ("AF_INET", "127.0.0.1", "127.0.0.1"),
    ("AF_INET", b"localhost", "127.0.0.1"),
    ("AF_INET", b"", "0.0.0.0"),
    ("AF_INET", b"127.0.0.1", "127.0.0.1"),
    ("AF_INET6", "localhost", "::1"),
    ("AF_INET6", "", "::"),
    ("AF_INET6", "::1", "::1"),
    ("AF_INET6", b"localhost", "::1"),
    ("AF_INET6", b"", "::"),
    ("AF_INET6", b"::1", "::1"),
])
def test_bind_passes_numeric_host_and_preserves_sockaddr_fields(tmp_path, family, host, expected):
    trailing = (0,) if family == "AF_INET" else (0, 17, 29)
    result = _run_probe(tmp_path, f"""
        import socket

        delegated = []
        def original_bind(sock, address):
            delegated.append(address)
            raise OSError('controlled numeric bind; no system call was made')
        socket.socket.bind = original_bind

        class BoundSocket:
            family = socket.{family}

        def test_numeric_boundary():
            try:
                socket.socket.bind(BoundSocket(), ({host!r}, *{trailing!r}))
            except OSError:
                pass
            assert delegated == [({expected!r}, *{trailing!r})]
    """)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout


@pytest.mark.parametrize("family, host", [("AF_INET", "::1"), ("AF_INET6", "127.0.0.1")])
def test_bind_rejects_wrong_ip_family_before_original_method(tmp_path, family, host):
    result = _run_probe(tmp_path, f"""
        import socket
        import pytest

        delegated = []
        def original_bind(sock, address):
            delegated.append(address)
            raise socket.gaierror(socket.EAI_FAMILY, 'wrong address family')
        socket.socket.bind = original_bind

        class BoundSocket:
            family = socket.{family}

        def test_family_error_is_local():
            with pytest.raises(socket.gaierror) as error:
                socket.socket.bind(BoundSocket(), ({host!r}, 0))
            assert error.value.errno == socket.EAI_FAMILY
            assert delegated == [], 'wrong-family host reached original bind'
    """)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout


def test_bind_preserves_unix_socket_address(tmp_path):
    if not hasattr(socket, "AF_UNIX"):
        pytest.skip("AF_UNIX is unavailable on this platform")
    result = _run_probe(tmp_path, """
        import socket

        delegated = []
        def original_bind(sock, address):
            delegated.append(address)
            raise OSError('controlled Unix bind; no system call was made')
        socket.socket.bind = original_bind

        class BoundSocket:
            family = socket.AF_UNIX

        def test_unix_boundary():
            try:
                socket.socket.bind(BoundSocket(), '/tmp/owned-fixture.sock')
            except OSError:
                pass
            assert delegated == ['/tmp/owned-fixture.sock']
    """)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout


@pytest.mark.parametrize("kind", ["SOCK_STREAM", "SOCK_DGRAM"])
def test_localhost_bind_preserves_real_owned_transport(tmp_path, kind):
    canary = f"bound-{uuid.uuid4().hex}".encode("ascii")
    result = _run_probe(tmp_path, f"""
        import socket

        def test_owned_bound_transport():
            with socket.socket(socket.AF_INET, socket.{kind}) as server:
                server.bind(('localhost', 0))
                server.settimeout(1)
                assert server.getsockname()[0] == '127.0.0.1'
                address = ('localhost', server.getsockname()[1])
                if socket.{kind} == socket.SOCK_STREAM:
                    server.listen()
                    with socket.create_connection(address) as client:
                        client.sendall({canary!r})
                        accepted, _ = server.accept()
                        with accepted:
                            assert accepted.recv(128) == {canary!r}
                else:
                    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
                        client.sendto({canary!r}, address)
                        assert server.recv(128) == {canary!r}
    """)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout


@pytest.mark.parametrize("flags", [0, socket.NI_NUMERICHOST, socket.NI_NUMERICSERV])
def test_getnameinfo_rejects_resolver_modes_without_libc(tmp_path, flags):
    result = _run_probe(tmp_path, f"""
        import socket

        calls = []
        def system_resolver(address, flags):
            calls.append((address, flags))
            return 'resolver-host', 'resolver-service'
        socket.getnameinfo = system_resolver

        def test_no_reverse_resolution():
            try:
                socket.getnameinfo(('127.0.0.2', 80), {flags!r})
            except OSError:
                pass
            assert calls == [], 'reverse name/service lookup reached the system resolver'
    """)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "1 passed, 1 error" in result.stdout
    assert "Unexpected network attempts" in result.stdout
    assert "getnameinfo" in result.stdout


@pytest.mark.parametrize("address", [("127.0.0.2", 80), ("::1", 80, 0, 0)])
def test_getnameinfo_numeric_mode_remains_usable(tmp_path, address):
    result = _run_probe(tmp_path, f"""
        import socket

        system_resolver = socket.getnameinfo
        calls = []
        def numeric_resolver(address, flags):
            calls.append((address, flags))
            assert flags & socket.NI_NUMERICHOST
            assert flags & socket.NI_NUMERICSERV
            return system_resolver(address, flags)
        socket.getnameinfo = numeric_resolver

        def test_numeric_answer():
            flags = socket.NI_NUMERICHOST | socket.NI_NUMERICSERV
            assert socket.getnameinfo({address!r}, flags) == ({address[0]!r}, '80')
            assert calls == [({address!r}, flags)]
    """)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout


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


def test_guard_restores_every_patched_socket_function(tmp_path):
    result = _run_probe(tmp_path, """
        import atexit
        import socket

        socket_names = ['bind', 'close', 'detach', 'connect', 'connect_ex', 'sendto']
        if hasattr(socket.socket, 'sendmsg'):
            socket_names.append('sendmsg')
        module_names = ['create_connection', 'getaddrinfo', 'gethostbyname',
                        'gethostbyname_ex', 'gethostbyaddr', 'getnameinfo']
        originals = [(socket.socket, name, getattr(socket.socket, name)) for name in socket_names]
        originals += [(socket, name, getattr(socket, name)) for name in module_names]

        def verify_restored():
            for owner, name, original in originals:
                assert getattr(owner, name) is original, f'{name} was not restored'
            print(f'restored-{len(originals)}-socket-functions')
        atexit.register(verify_restored)

        def test_owned_transport():
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as server, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
                server.bind(('127.0.0.1', 0))
                server.settimeout(1)
                sender.sendto(b'restoration transport', server.getsockname())
                assert server.recv(64) == b'restoration transport'
    """)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout
    count = 12 + int(hasattr(socket.socket, "sendmsg"))
    assert f"restored-{count}-socket-functions" in result.stdout, result.stdout + result.stderr


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


def test_worker_hostname_url_does_not_delegate_to_libc(tmp_path):
    result = _run_probe(tmp_path, """
        import socket
        import sys
        import types
        from urllib.request import Request

        package = types.ModuleType('lib')
        package.__path__ = []
        worker = types.ModuleType('lib.bounded_get')
        launches = []
        worker.get = lambda req, **kwargs: launches.append(req.full_url)
        sys.modules['lib'] = package
        sys.modules['lib.bounded_get'] = worker

        system_resolver = socket.getaddrinfo
        lookups = []
        def resolver(host, port, family=0, type=0, proto=0, flags=0):
            lookups.append(host)
            numeric_host = '127.0.0.1' if host == 'localhost' else host
            return system_resolver(numeric_host, port, family, type, proto,
                                   flags | socket.AI_NUMERICHOST | socket.AI_NUMERICSERV)
        socket.getaddrinfo = resolver

        def test_no_worker_hostname_resolution():
            with socket.socket() as server:
                server.bind(('127.0.0.1', 0))
                url = f'http://localhost:{server.getsockname()[1]}/fixture'
                try:
                    worker.get(Request(url), timeout=1, deadline_monotonic=123)
                except OSError:
                    pass
                assert 'localhost' not in lookups, 'worker hostname reached the system resolver'
                assert launches == []
    """)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "1 passed, 1 error" in result.stdout
    assert "Unexpected network attempts" in result.stdout
    assert "bounded_get" in result.stdout
