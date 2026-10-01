"""Modules that build their own Request must not leak credentials on redirect.

``lib/http.py`` installs a redirect handler that drops credential headers when a
3xx changes origin, but that only protects callers that go through it. Several
modules build a ``urllib.request.Request`` themselves; ``github.py`` and
``transcribe.py`` attach a bearer token to theirs, so they must use
``http.open_request`` rather than ``urllib.request.urlopen``.
"""

import ast
import http.server
import pathlib
import socketserver
import threading
from urllib.request import Request

from lib import http as l30d_http


class _CaptureHandler(http.server.BaseHTTPRequestHandler):
    captured: dict = {}

    def do_GET(self):
        type(self).captured = {k.lower(): v for k, v in self.headers.items()}
        body = b'{"ok":true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


def _serve(handler):
    httpd = socketserver.TCPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def test_open_request_strips_bearer_across_origin():
    attacker = _serve(_CaptureHandler)
    _CaptureHandler.captured = {}

    class Victim(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header(
                "Location", f"http://127.0.0.1:{attacker.server_address[1]}/steal"
            )
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *_args):
            pass

    victim = _serve(Victim)
    req = Request(
        f"http://127.0.0.1:{victim.server_address[1]}/start",
        headers={"Authorization": "Bearer ghp_sentinel_token"},
    )
    try:
        with l30d_http.open_request(req, 10) as resp:
            resp.read()
    finally:
        victim.shutdown()
        attacker.shutdown()

    assert "authorization" not in _CaptureHandler.captured, (
        "bearer token survived a cross-origin redirect: "
        f"{_CaptureHandler.captured.get('authorization')!r}"
    )


def _dotted_name(node) -> str:
    """Render a dotted attribute chain (``urllib.request``) or "" if not one."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return ""
    parts.append(node.id)
    return ".".join(reversed(parts))


def _direct_urlopen_calls(source: str) -> list[int]:
    """Line numbers of calls that reach ``urllib.request.urlopen`` in ``source``.

    Resolved through the module's imports rather than by text match. Searching
    for the literal ``urllib.request.urlopen(`` would miss
    ``from urllib.request import urlopen``, a form this repository already uses
    in four other modules, so a future edit to a credential-bearing module could
    reintroduce the bypass while keeping the guard green.
    """
    tree = ast.parse(source)

    # Names bound to the urlopen function itself.
    urlopen_names: set[str] = set()
    # Names bound to the urllib.request module.
    module_names: set[str] = {"urllib.request"}

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module == "urllib.request":
                for alias in node.names:
                    if alias.name == "urlopen":
                        urlopen_names.add(alias.asname or alias.name)
            elif node.module == "urllib":
                for alias in node.names:
                    if alias.name == "request":
                        module_names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "urllib.request":
                    # `import urllib.request` binds "urllib"; an asname binds that.
                    module_names.add(alias.asname or "urllib.request")
                elif alias.name == "urllib":
                    module_names.add(f"{alias.asname or 'urllib'}.request")

    hits: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id in urlopen_names:
            hits.append(node.lineno)
        elif isinstance(func, ast.Attribute) and func.attr == "urlopen":
            if _dotted_name(func.value) in module_names:
                hits.append(node.lineno)
    return sorted(hits)


def test_credential_bearing_modules_do_not_call_urlopen_directly():
    """Regression guard: these modules attach a bearer token to their Request."""
    lib = pathlib.Path(l30d_http.__file__).parent
    for name in ("github.py", "transcribe.py"):
        source = (lib / name).read_text(encoding="utf-8")
        hits = _direct_urlopen_calls(source)
        assert hits == [], (
            f"{name} calls urllib.request.urlopen directly at line(s) "
            f"{hits}, bypassing the cross-origin credential strip in "
            "http.open_request"
        )


def test_urlopen_guard_detects_aliased_forms():
    """The guard itself must not be fooled by an import alias.

    Without this, the guard could silently stop working: a text search for
    ``urllib.request.urlopen(`` passes on every aliased form below.
    """
    detected = (
        "import urllib.request\nurllib.request.urlopen(req)\n",
        "from urllib.request import urlopen\nurlopen(req)\n",
        "from urllib.request import urlopen as uo\nuo(req)\n",
        "from urllib import request\nrequest.urlopen(req)\n",
        "from urllib import request as r\nr.urlopen(req)\n",
        "import urllib.request as ur\nur.urlopen(req)\n",
    )
    for source in detected:
        assert _direct_urlopen_calls(source), f"missed: {source!r}"

    ignored = (
        # Routed through the protected wrapper — the whole point of the fix.
        "from . import http\nhttp.open_request(req, timeout=10)\n",
        # An unrelated urlopen on some other object must not trip the guard.
        "session.urlopen(req)\n",
        "from urllib.request import Request\nRequest(url)\n",
    )
    for source in ignored:
        assert not _direct_urlopen_calls(source), f"false positive: {source!r}"
