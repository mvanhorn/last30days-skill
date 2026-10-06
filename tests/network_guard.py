"""Fail offline tests for attempted network access, including caught errors."""

import errno
import importlib
import ipaddress
import socket
import threading
import weakref
from urllib.parse import urlsplit

import pytest


def _loopback(host):
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@pytest.fixture(autouse=True)
def network_guard():
    attempts = []
    owned_sockets = weakref.WeakKeyDictionary()
    ownership_lock = threading.RLock()
    original_bind = socket.socket.bind
    original_close = socket.socket.close
    original_detach = socket.socket.detach
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_sendto = socket.socket.sendto
    original_sendmsg = getattr(socket.socket, "sendmsg", None)
    original_create_connection = socket.create_connection
    original_getaddrinfo = socket.getaddrinfo
    original_gethostbyname = socket.gethostbyname
    original_gethostbyname_ex = socket.gethostbyname_ex
    original_gethostbyaddr = socket.gethostbyaddr

    def deny(operation, address):
        attempts.append(f"{operation}: {address!r}")
        raise OSError(errno.ENETUNREACH, "offline test attempted an unowned network destination")

    def endpoint(family, kind, protocol, address):
        kind = int(kind) & ~(getattr(socket, "SOCK_NONBLOCK", 0) | getattr(socket, "SOCK_CLOEXEC", 0))
        if not protocol:
            protocol = {socket.SOCK_STREAM: socket.IPPROTO_TCP, socket.SOCK_DGRAM: socket.IPPROTO_UDP}.get(kind, 0)
        scope = address[3] if family == socket.AF_INET6 and len(address) > 3 else 0
        return family, str(ipaddress.ip_address(address[0])), address[1], scope, kind, protocol

    def live_endpoints():
        with ownership_lock:
            return {value for sock, value in owned_sockets.items() if sock.fileno() >= 0}

    def destinations(operation, host, port, family=0, kind=0, protocol=0, flags=0):
        if not _loopback(host):
            deny(operation, (host, port))
        owned = live_endpoints()
        hosts = {entry[1] for entry in owned} if host == "localhost" else {host}
        answers = []
        for address in sorted(hosts):
            try:
                candidates = original_getaddrinfo(address, port, family, kind, protocol, flags)
            except socket.gaierror:
                continue
            for item in candidates:
                if endpoint(item[0], item[1], item[2], item[4]) in owned and item not in answers:
                    answers.append(item)
        if not answers:
            deny(operation, (host, port))
        return answers

    def checked_address(operation, sock, address):
        if sock.family not in (socket.AF_INET, socket.AF_INET6):
            return address
        return destinations(operation, address[0], address[1], sock.family, sock.type, sock.proto)[0][4]

    def bind(sock, address):
        result = original_bind(sock, address)
        if sock.family in (socket.AF_INET, socket.AF_INET6) and _loopback(address[0]):
            with ownership_lock:
                owned_sockets[sock] = endpoint(sock.family, sock.type, sock.proto, sock.getsockname())
        return result

    def close(sock):
        with ownership_lock:
            owned_sockets.pop(sock, None)
        return original_close(sock)

    def detach(sock):
        with ownership_lock:
            owned_sockets.pop(sock, None)
        return original_detach(sock)

    def connect(sock, address):
        return original_connect(sock, checked_address("connect", sock, address))

    def connect_ex(sock, address):
        return original_connect_ex(sock, checked_address("connect_ex", sock, address))

    def sendto(sock, data, *args):
        address = checked_address("sendto", sock, args[-1])
        return original_sendto(sock, data, *args[:-1], address)

    def sendmsg(sock, buffers, ancdata=(), flags=0, address=None):
        if address is None:
            return original_sendmsg(sock, buffers, ancdata, flags)
        return original_sendmsg(sock, buffers, ancdata, flags, checked_address("sendmsg", sock, address))

    def create_connection(address, *args, **kwargs):
        destinations("create_connection", address[0], address[1], kind=socket.SOCK_STREAM)
        return original_create_connection(address, *args, **kwargs)

    def getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        return destinations("getaddrinfo", host, port, family, type, proto, flags)

    def gethostbyname(host):
        if not _loopback(host):
            deny("gethostbyname", host)
        return original_gethostbyname(host)

    def gethostbyname_ex(host):
        if not _loopback(host):
            deny("gethostbyname_ex", host)
        return original_gethostbyname_ex(host)

    def gethostbyaddr(host):
        if not _loopback(host):
            deny("gethostbyaddr", host)
        return original_gethostbyaddr(host)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(socket.socket, "bind", bind)
        patch.setattr(socket.socket, "close", close)
        patch.setattr(socket.socket, "detach", detach)
        patch.setattr(socket.socket, "connect", connect)
        patch.setattr(socket.socket, "connect_ex", connect_ex)
        patch.setattr(socket.socket, "sendto", sendto)
        if original_sendmsg is not None:
            patch.setattr(socket.socket, "sendmsg", sendmsg)
        patch.setattr(socket, "create_connection", create_connection)
        patch.setattr(socket, "getaddrinfo", getaddrinfo)
        patch.setattr(socket, "gethostbyname", gethostbyname)
        patch.setattr(socket, "gethostbyname_ex", gethostbyname_ex)
        patch.setattr(socket, "gethostbyaddr", gethostbyaddr)
        try:
            bounded_get = importlib.import_module("lib.bounded_get")
        except ModuleNotFoundError as error:
            if error.name not in ("lib", "lib.bounded_get"):
                raise
        else:
            original_get = bounded_get.get

            def worker_get(req, *args, **kwargs):
                target = urlsplit(req.full_url)
                address = (target.hostname, target.port if target.port is not None else (443 if target.scheme == "https" else 80))
                destinations("bounded_get", *address, kind=socket.SOCK_STREAM)
                if target.hostname == "localhost":
                    # Isolated workers resolve the hostname outside the parent socket patches.
                    owned = live_endpoints()
                    candidates = original_getaddrinfo(*address, type=socket.SOCK_STREAM)
                    if any(endpoint(item[0], item[1], item[2], item[4]) not in owned for item in candidates):
                        deny("bounded_get", address)
                return original_get(req, *args, **kwargs)

            patch.setattr(bounded_get, "get", worker_get)
        yield
    if attempts:
        pytest.fail("Unexpected network attempts:\n" + "\n".join(attempts))
