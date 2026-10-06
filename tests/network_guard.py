"""Fail offline tests for attempted network access, including caught errors."""

import errno
import importlib
import ipaddress
import socket
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
    owned_sockets = {}
    original_bind = socket.socket.bind
    original_close = socket.socket.close
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_sendto = socket.socket.sendto
    original_create_connection = socket.create_connection
    original_getaddrinfo = socket.getaddrinfo
    original_gethostbyname = socket.gethostbyname
    original_gethostbyname_ex = socket.gethostbyname_ex
    original_gethostbyaddr = socket.gethostbyaddr

    def deny(operation, address):
        attempts.append(f"{operation}: {address!r}")
        raise OSError(errno.ENETUNREACH, "offline test attempted an unowned network destination")

    def owned(address):
        return (
            isinstance(address, tuple)
            and len(address) >= 2
            and _loopback(address[0])
            and address[1] in owned_sockets.values()
        )

    def bind(sock, address):
        result = original_bind(sock, address)
        if sock.family in (socket.AF_INET, socket.AF_INET6) and _loopback(address[0]):
            owned_sockets[id(sock)] = sock.getsockname()[1]
        return result

    def close(sock):
        owned_sockets.pop(id(sock), None)
        return original_close(sock)

    def connect(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6) and not owned(address):
            deny("connect", address)
        return original_connect(sock, address)

    def connect_ex(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6) and not owned(address):
            deny("connect_ex", address)
        return original_connect_ex(sock, address)

    def sendto(sock, data, *args):
        if sock.family in (socket.AF_INET, socket.AF_INET6) and not owned(args[-1]):
            deny("sendto", args[-1])
        return original_sendto(sock, data, *args)

    def create_connection(address, *args, **kwargs):
        if not owned(address):
            deny("create_connection", address)
        return original_create_connection(address, *args, **kwargs)

    def getaddrinfo(host, port, *args, **kwargs):
        if not owned((host, port)):
            deny("getaddrinfo", (host, port))
        return original_getaddrinfo(host, port, *args, **kwargs)

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
        patch.setattr(socket.socket, "connect", connect)
        patch.setattr(socket.socket, "connect_ex", connect_ex)
        patch.setattr(socket.socket, "sendto", sendto)
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
                address = (target.hostname, target.port or (443 if target.scheme == "https" else 80))
                if not owned(address):
                    deny("bounded_get", address)
                return original_get(req, *args, **kwargs)

            patch.setattr(bounded_get, "get", worker_get)
        yield
    if attempts:
        pytest.fail("Unexpected network attempts:\n" + "\n".join(attempts))
