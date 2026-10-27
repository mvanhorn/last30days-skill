"""Per-build tool-boundary audit for the grok CLI.

Grok X research runs the CLI with permissions bypassed and an untrusted topic
in the prompt, so last30days must know the child has no local tools. Grok
ships a new build most days, so instead of pinning one audited build, each
build is audited once per machine before it receives credentials:

* the real native binary runs the exact research command line against a local
  fake xAI endpoint (``127.0.0.1``) with a dummy key and a throwaway HOME, so
  the audit costs nothing and never contacts xAI;
* the main model request must declare hosted X search and no other tool;
* the fake model then forces a shell call and a file read, and the CLI must
  refuse both (no canary file, no secret in any later request);
* the same run with a permissive profile must execute both calls, proving the
  fixture can detect a leak (otherwise the result is inconclusive);
* all HTTP(S) traffic goes through a refusing proxy, and any host other than
  the fixture fails the audit.

Pass and fail verdicts are cached on disk keyed by the native binary's sha256,
its ``--version`` line, and ``AUDIT_SCHEMA``. Inconclusive results are only
memoized for this process, so the next run audits again.
"""

import base64
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import env, log

# Bump when the checks change so verdicts recorded by older checks re-audit.
AUDIT_SCHEMA = 3
CACHE_FILENAME = "grok-audit.json"

PASS = "pass"
FAIL = "fail"
INCONCLUSIVE = "inconclusive"

# A single forced turn takes 2-6s on a warm machine; the cap keeps a wedged
# child from consuming the whole X lane.
_CHILD_TIMEOUT_SECONDS = 45
_EXPECTED_TOOLS = [{"type": "x_search"}]
_DUMMY_KEY = "xai-last30days-audit-dummy"
# grok's public OIDC client id, used when the user's store names none.
_DEFAULT_OIDC_CLIENT_ID = "b1a00492-073a-47ea-816f-4c329264a828"


def _log(msg: str) -> None:
    log.source_log("Grok", msg, tty_only=False)


@dataclass(frozen=True)
class Verdict:
    status: str
    check: str = ""

    @property
    def passed(self) -> bool:
        return self.status == PASS


# --- native binary resolution ----------------------------------------------

def _npm_package_dir(binary: Path) -> Optional[Path]:
    """The @xai-official/grok package behind an npm launcher, if this is one."""
    if binary.suffix.lower() in (".cmd", ".ps1", ".bat"):
        candidate = binary.parent / "node_modules" / "@xai-official" / "grok"
    else:
        try:
            with open(binary, "rb") as fh:
                if fh.read(2) != b"#!":
                    return None
        except OSError:
            return None
        candidate = binary.parent.parent
    try:
        meta = json.loads((candidate / "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return candidate if meta.get("name") == "@xai-official/grok" else None


def _platform_tag() -> str:
    machine = platform.machine().lower()
    arch = {"x86_64": "x64", "amd64": "x64", "aarch64": "arm64"}.get(machine, machine)
    return f"{sys.platform}-{arch}"


def _decompress_brotli(src: Path, dest: Path, timeout: float) -> bool:
    """Decompress the npm-shipped ``grok.br`` with node (Python has no brotli)."""
    node = shutil.which("node")
    if node is None or timeout <= 1:
        return False
    tmp = dest.with_name(dest.name + f".tmp.{os.getpid()}.{os.urandom(4).hex()}")
    script = (
        "const z=require('zlib'),f=require('fs');"
        "f.writeFileSync(process.argv[2],z.brotliDecompressSync(f.readFileSync(process.argv[1])))"
    )
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([node, "-e", script, str(src), str(tmp)], check=True,
                       capture_output=True, timeout=min(60.0, timeout))
        os.chmod(tmp, 0o755)
        os.replace(tmp, dest)
        return True
    except (OSError, subprocess.SubprocessError):
        return False
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def resolve_native(binary: str, decompress: bool = True, timeout: float = 60.0) -> Optional[str]:
    """The native grok executable that ``binary`` would run, or None.

    A native install resolves to itself. The npm package installs a node
    launcher that is byte-identical across releases and picks the real binary
    at run time from HOME, so hashing or exec'ing the launcher would let one
    audit cover every later build. Resolve the build the package ships
    instead: the copy its launcher writes to ``$GROK_HOME/bin``, else the
    platform package binary, decompressing the brotli copy into the
    last30days config dir once (skipped when ``decompress`` is False, so
    no-subprocess callers like doctor only find an existing copy).
    """
    path = Path(binary)
    package = _npm_package_dir(path)
    if package is None:
        return binary
    try:
        version = json.loads((package / "package.json").read_text(encoding="utf-8"))["version"]
    except (OSError, ValueError, KeyError):
        return None
    exe = ".exe" if os.name == "nt" else ""
    grok_home = Path(os.environ.get("GROK_HOME") or Path.home() / ".grok")
    installed = grok_home / "bin" / f"grok-{version}{exe}"
    if installed.is_file():
        return str(installed)
    platform_pkg = f"grok-{_platform_tag()}"
    for base in (package / "node_modules" / "@xai-official", package.parent):
        native = base / platform_pkg / "bin" / f"grok{exe}"
        if native.is_file():
            return str(native)
        compressed = native.with_name(native.name + ".br")
        if compressed.is_file():
            # The launcher's own cache dir when last30days has no config dir.
            cache = env.CONFIG_DIR / "grok-bin" if env.CONFIG_DIR is not None else grok_home / "bin"
            dest = cache / f"grok-{version}{exe}"
            if dest.is_file() or (decompress and _decompress_brotli(compressed, dest, timeout)):
                return str(dest)
    return None


def _file_digest(binary: str) -> str:
    digest = hashlib.sha256()
    with open(binary, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_key(binary: str, version_line: str) -> str:
    return f"{_file_digest(binary)}|{version_line}|{AUDIT_SCHEMA}"


# --- verdict cache ----------------------------------------------------------

_MEMO: Dict[str, Verdict] = {}
_LOCK = threading.Lock()


def _cache_path() -> Optional[Path]:
    return env.CONFIG_DIR / CACHE_FILENAME if env.CONFIG_DIR is not None else None


def _read_cache() -> Dict[str, Any]:
    path = _cache_path()
    if path is None:
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_cache(data: Dict[str, Any]) -> None:
    path = _cache_path()
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + f".tmp.{os.getpid()}")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        _log(f"could not save grok audit verdict: {exc}")


def cached_verdict(key: str) -> Optional[Verdict]:
    entry = _read_cache().get(key)
    if not isinstance(entry, dict) or entry.get("verdict") not in (PASS, FAIL):
        return None
    return Verdict(entry["verdict"], str(entry.get("failed_check") or ""))


def cached_status(binary: str) -> Optional[Dict[str, str]]:
    """Doctor's view: the cached verdict for the build on PATH, if any.

    File reads only, no subprocess: the native build is located without
    decompressing and matched by hash, since its ``--version`` line needs a
    run.
    """
    native = resolve_native(binary, decompress=False)
    if native is None:
        return None
    try:
        digest = _file_digest(native)
    except OSError:
        return None
    for key, entry in _read_cache().items():
        parts = key.split("|")
        if (len(parts) == 3 and parts[0] == digest and parts[2] == str(AUDIT_SCHEMA)
                and isinstance(entry, dict) and entry.get("verdict") in (PASS, FAIL)):
            return {"verdict": entry["verdict"], "version": str(entry.get("version") or ""),
                    "check": str(entry.get("failed_check") or "")}
    return None


def clear_failed() -> int:
    """Drop cached fail verdicts so the next run re-audits. Returns the count."""
    with _LOCK:
        data = _read_cache()
        kept = {k: v for k, v in data.items() if not (isinstance(v, dict) and v.get("verdict") == FAIL)}
        removed = len(data) - len(kept)
        if removed:
            _write_cache(kept)
        _MEMO.clear()
        return removed


def verdict_for(binary: str, version_line: str, deadline: float) -> Verdict:
    """Cached or fresh audit verdict for one native build."""
    try:
        key = build_key(binary, version_line)
    except OSError as exc:
        return Verdict(INCONCLUSIVE, f"could not read the grok binary: {exc}")
    with _LOCK:
        if key in _MEMO:
            return _MEMO[key]
        verdict = cached_verdict(key)
        if verdict is None:
            _log(f"auditing new grok build {version_line} (local, no credits)")
            verdict = run_audit(binary, deadline)
            _log(f"grok build audit: {verdict.status}" + (f" ({verdict.check})" if verdict.check else ""))
            if verdict.status in (PASS, FAIL):
                data = _read_cache()
                data[key] = {
                    "verdict": verdict.status,
                    "failed_check": verdict.check,
                    "version": version_line,
                    "audited_at": datetime.now(timezone.utc).isoformat(),
                }
                _write_cache(data)
        _MEMO[key] = verdict
        return verdict


# --- local fixture ----------------------------------------------------------

def _model_info(base: str) -> Dict[str, Any]:
    # Field set mirrors the catalog entries cli-chat-proxy serves; the CLI
    # rejects entries missing any of them.
    return {
        "id": "grok-4.7", "model": "grok-4.7", "model_family": "xai", "base_url": base,
        "name": "Grok 4.7", "description": "last30days audit fixture",
        "max_completion_tokens": None, "temperature": None, "top_p": None,
        "api_backend": "responses", "auth_scheme": "bearer", "extra_headers": {},
        "context_window": 256000, "context_windows": [256000],
        "auto_compact_threshold_percent": 80, "system_prompt_label": "Grok 4.7",
        "use_concise": False, "agent_type": "grok-build-plan",
        "inference_idle_timeout_secs": None, "max_retries": None,
        "subagent_rate_limit_max_attempts": None, "hidden": False, "supported_in_api": True,
        "reasoning_effort": "high", "supports_reasoning_effort": True,
        "reasoning_efforts": [{"id": "high", "value": "high", "label": "High",
                               "description": "", "default": True}],
        "supports_backend_search": True, "compactions_remaining": 1,
        "compaction_at_tokens": True, "show_model_fingerprint": False,
        "stream_tool_calls": None,
        "laziness_detector": {"enabled": False, "max_nudges_per_session": 0,
                              "idle_threshold_ms": None, "min_confidence": None,
                              "include_reasoning": None},
    }


def _sse(output: List[Dict[str, Any]], rid: str) -> bytes:
    resp = {
        "id": rid, "object": "response", "created_at": int(time.time()), "status": "completed",
        "model": "grok-4.7", "output": output,
        "usage": {"input_tokens": 1, "input_tokens_details": {"cached_tokens": 0},
                  "output_tokens": 1, "output_tokens_details": {"reasoning_tokens": 0},
                  "total_tokens": 2},
    }
    events = [{"type": "response.created", "response": dict(resp, status="in_progress", output=[])}]
    for index, item in enumerate(output):
        events.append({"type": "response.output_item.added", "output_index": index, "item": item})
        events.append({"type": "response.output_item.done", "output_index": index, "item": item})
    events.append({"type": "response.completed", "response": resp})
    chunks = []
    for seq, event in enumerate(events):
        event["sequence_number"] = seq
        chunks.append(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n")
    return "".join(chunks).encode()


class _Fixture:
    """Fake xAI endpoint plus a refusing proxy, both on 127.0.0.1."""

    def __init__(self, forced_calls: List[Dict[str, Any]]):
        self.forced_calls = forced_calls
        self.requests: List[Dict[str, Any]] = []
        self.proxy_hosts: List[str] = []
        self._lock = threading.Lock()
        fixture = self

        class Api(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def _send(self, code, payload, ctype="application/json"):
                data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
                self.send_response(code)
                self.send_header("content-type", ctype)
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                if self.path.rstrip("/").endswith("/models"):
                    info = _model_info(fixture.base)
                    return self._send(200, {"object": "list", "data": [info], "models": [info]})
                self._send(404, {"error": "not found"})

            def do_POST(self):
                length = int(self.headers.get("content-length") or 0)
                raw = self.rfile.read(length) if length else b""
                try:
                    body = json.loads(raw or b"{}")
                except ValueError:
                    body = {}
                if not self.path.endswith("/responses"):
                    return self._send(404, {"error": "not found"})
                tools = body.get("tools") or []
                if any(isinstance(t, dict) and t.get("name") == "session_title" for t in tools):
                    item = {"type": "function_call", "id": "fc_title", "call_id": "call_title",
                            "name": "session_title", "status": "completed",
                            "arguments": json.dumps({"session_title": "audit"})}
                    return self._send(200, _sse([item], "resp_title"), "text/event-stream")
                with fixture._lock:
                    fixture.requests.append({"raw": raw.decode("utf-8", "replace"), "body": body})
                    first = len(fixture.requests) == 1
                if first:
                    output = [dict(call, type="function_call", status="completed", id=f"fc_{i}")
                              for i, call in enumerate(fixture.forced_calls)]
                else:
                    output = [{"type": "message", "id": "msg_done", "role": "assistant",
                               "status": "completed",
                               "content": [{"type": "output_text", "text": "no posts", "annotations": []}]}]
                self._send(200, _sse(output, f"resp_{len(fixture.requests)}"), "text/event-stream")

        class Proxy(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _refuse(self):
                with fixture._lock:
                    fixture.proxy_hosts.append(self.path)
                self.send_response(403)
                self.send_header("content-length", "0")
                self.end_headers()

            do_CONNECT = do_GET = do_POST = do_PUT = do_HEAD = _refuse

        self._api = ThreadingHTTPServer(("127.0.0.1", 0), Api)
        self._proxy = ThreadingHTTPServer(("127.0.0.1", 0), Proxy)
        self.base = f"http://127.0.0.1:{self._api.server_address[1]}/v1"
        self.proxy = f"http://127.0.0.1:{self._proxy.server_address[1]}"

    def __enter__(self):
        for server in (self._api, self._proxy):
            threading.Thread(target=server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc):
        for server in (self._api, self._proxy):
            server.shutdown()
            server.server_close()


def _signed_in_client_id() -> str:
    """The OIDC client id of the user's own store, so the dummy session takes
    the same auth path the real one does. Reads the key name only."""
    from . import grok_x

    try:
        data = json.loads(grok_x.token_store_path().read_text(encoding="utf-8"))
        for key in data:
            if isinstance(key, str) and "::" in key:
                return key.rsplit("::", 1)[1]
    except (OSError, ValueError, AttributeError):
        pass
    return _DEFAULT_OIDC_CLIENT_ID


def _write_dummy_session(home: str) -> None:
    """A signed-in credential store shaped like the one _stage_child_home
    copies, holding a fake unexpired token. Production runs in this mode, so
    the audit must see the tools the build declares when signed in."""
    def b64(obj: Dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()

    client_id = _signed_in_client_id()
    zero = "00000000-0000-0000-0000-000000000000"
    token = (b64({"alg": "none", "typ": "JWT"}) + "."
             + b64({"sub": zero, "exp": 4070908800, "iat": 1761350400,
                    "iss": "https://auth.x.ai", "aud": client_id}) + ".audit")
    entry = {
        "key": token, "auth_mode": "oidc", "create_time": "2026-01-01T00:00:00Z",
        "user_id": zero, "email": "audit@example.invalid", "first_name": "Audit",
        "profile_image_asset_id": "", "principal_type": "user", "principal_id": zero,
        "team_id": zero, "coding_data_retention_opt_out": True,
        "refresh_token": "last30days-audit-dummy", "expires_at": "2099-01-01T00:00:00Z",
        "oidc_issuer": "https://auth.x.ai", "oidc_client_id": client_id,
    }
    store = Path(home) / ".grok"
    store.mkdir(mode=0o700, parents=True, exist_ok=True)
    (store / "auth.json").write_text(json.dumps({f"https://auth.x.ai::{client_id}": entry}),
                                     encoding="utf-8")


def _audit_env(home: str, fixture: _Fixture) -> Dict[str, str]:
    from . import grok_x

    child_env = grok_x._subprocess_env(home)
    child_env.update({
        "GROK_XAI_API_BASE_URL": fixture.base,
        "GROK_CLI_CHAT_PROXY_BASE_URL": fixture.base,
        "GROK_MODELS_BASE_URL": fixture.base,
        "GROK_MODELS_LIST_URL": fixture.base + "/models",
        "XAI_API_KEY": _DUMMY_KEY,
        "HTTPS_PROXY": fixture.proxy,
        "HTTP_PROXY": fixture.proxy,
        "https_proxy": fixture.proxy,
        "http_proxy": fixture.proxy,
        "NO_PROXY": "127.0.0.1,localhost",
        "no_proxy": "127.0.0.1,localhost",
    })
    return child_env


def _run_child(binary: str, permissive: bool, deadline: float, signed_in: bool = False) -> Dict[str, Any]:
    """One forced-call turn. Returns what the fixture observed."""
    from . import grok_x

    with tempfile.TemporaryDirectory(prefix="last30days-grok-audit-") as workdir:
        home = os.path.join(workdir, "home")
        os.makedirs(home)
        if signed_in:
            _write_dummy_session(home)
        canary = Path(workdir) / "canary"
        secret_value = "l30d-audit-" + os.urandom(8).hex()
        secret = Path(workdir) / "secret.txt"
        secret.write_text(secret_value, encoding="utf-8")
        forced = [
            {"call_id": "call_shell", "name": "run_terminal_command",
             "arguments": json.dumps({"command": f'echo audit > "{canary}"', "description": "audit canary"})},
            {"call_id": "call_read", "name": "read_file",
             "arguments": json.dumps({"target_file": str(secret)})},
        ]
        if permissive:
            agent_file = Path(workdir) / "control-agent.md"
            agent_file.write_text('---\n{"name":"control","description":"control"}\n---\ncontrol\n',
                                  encoding="utf-8")
            argv = [binary, "-p", "run the audit", "--permission-mode", "bypassPermissions",
                    "--output-format", "json", "--agent", str(agent_file)]
        else:
            agent_file = grok_x._write_agent_profile(workdir)
            argv = grok_x._research_argv(binary, "Use x_keyword_search for audit", agent_file)
        with _Fixture(forced) as fixture:
            remaining = min(_CHILD_TIMEOUT_SECONDS, deadline - time.monotonic())
            if remaining <= 1:
                return {"timeout": True}
            try:
                subprocess.run(argv, capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=remaining, cwd=workdir,
                               env=_audit_env(home, fixture))
            except subprocess.TimeoutExpired:
                return {"timeout": True}
            except OSError as exc:
                return {"error": f"{type(exc).__name__}: {exc}"}
            requests = list(fixture.requests)
            hosts = list(fixture.proxy_hosts)
        return {
            "requests": requests,
            "proxy_hosts": hosts,
            "canary": canary.exists(),
            "leaked": any(secret_value in r["raw"] for r in requests[1:]),
            "answered": _answered_call_ids(requests[1:]) >= {c["call_id"] for c in forced},
        }


def _answered_call_ids(requests: List[Dict[str, Any]]) -> set:
    """Forced calls the CLI reported a result for in a follow-up turn."""
    answered = set()
    for request in requests:
        for item in request["body"].get("input") or []:
            if isinstance(item, dict) and item.get("type") == "function_call_output":
                answered.add(item.get("call_id"))
    return answered


def _check_restricted(run: Dict[str, Any]) -> Optional[Verdict]:
    """The verdict one restricted run forces, or None when it refused cleanly."""
    if run.get("timeout"):
        return Verdict(INCONCLUSIVE, "audit timed out")
    if run.get("error"):
        return Verdict(INCONCLUSIVE, run["error"])
    if run["proxy_hosts"]:
        return Verdict(FAIL, "contacted a host other than the local fixture: "
                       + ", ".join(sorted(set(run["proxy_hosts"]))[:3]))
    if not run["requests"]:
        return Verdict(INCONCLUSIVE, "the CLI sent no research request to the fixture")
    # The first turn must declare exactly hosted X search; no later turn may
    # add anything to it (a follow-up may resend the same list or omit it).
    for index, request in enumerate(run["requests"]):
        tools = request["body"].get("tools") or []
        if (tools != _EXPECTED_TOOLS) if index == 0 else any(t != _EXPECTED_TOOLS[0] for t in tools):
            names = sorted({str(t.get("name") or t.get("type")) if isinstance(t, dict) else str(t)
                            for t in tools})
            return Verdict(FAIL, "declared tools other than hosted X search: " + ", ".join(names[:8]))
    if run["canary"]:
        return Verdict(FAIL, "executed a forced shell command")
    if run["leaked"]:
        return Verdict(FAIL, "returned a forced file read")
    if not run["answered"]:
        # A crash or early exit is not evidence the boundary refused the calls.
        return Verdict(INCONCLUSIVE, "the CLI did not answer the forced calls")
    return None


def run_audit(binary: str, deadline: float) -> Verdict:
    """Audit one native build against the local fixture. Never raises."""
    try:
        # Production runs signed in; the API-key run covers XAI_API_KEY users.
        for signed_in in (True, False):
            verdict = _check_restricted(_run_child(binary, permissive=False, deadline=deadline,
                                                   signed_in=signed_in))
            if verdict is not None:
                return verdict
        control = _run_child(binary, permissive=True, deadline=deadline)
        if control.get("timeout"):
            return Verdict(INCONCLUSIVE, "audit timed out")
        if control.get("error") or not (control.get("canary") and control.get("leaked")):
            return Verdict(INCONCLUSIVE, "positive control did not execute the forced calls")
        return Verdict(PASS)
    except Exception as exc:  # noqa: BLE001 - the audit must never break a run
        return Verdict(INCONCLUSIVE, f"{type(exc).__name__}: {exc}")
