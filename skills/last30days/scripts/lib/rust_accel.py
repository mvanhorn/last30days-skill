"""Optional native Rust accelerator bridge with transparent Python fallback.

Discovers a standalone ``last30days-core`` binary (no Rust toolchain required
on the user's machine) and communicates over a persistent newline-delimited
JSON stdio worker. When no binary is installed or ``LAST30DAYS_ENGINE=python``
is set, callers fall back seamlessly to the pure-Python implementations.
"""

from __future__ import annotations

import atexit
import json
import math
import os
import select
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

PROTOCOL_VERSION = 1
DEFAULT_TIMEOUT_SECONDS = 30.0

_BINARY_NAME = "last30days-core.exe" if sys.platform == "win32" else "last30days-core"


class RustAccelError(RuntimeError):
    """Raised when LAST30DAYS_ENGINE=rust is forced and the Rust worker fails."""


def engine_mode() -> str:
    """Return configured acceleration mode: ``'auto'``, ``'rust'``, or ``'python'``."""
    raw = (os.environ.get("LAST30DAYS_ENGINE") or "auto").strip().lower()
    if raw in {"python", "py", "off", "0", "false"}:
        return "python"
    if raw in {"rust", "rs", "force", "required"}:
        return "rust"
    return "auto"


def worker_timeout() -> float:
    """Return per-request timeout in seconds for the Rust stdio worker."""
    raw = (os.environ.get("LAST30DAYS_RUST_TIMEOUT") or "").strip()
    if not raw:
        return DEFAULT_TIMEOUT_SECONDS
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS
    if not math.isfinite(value) or value <= 0:
        return DEFAULT_TIMEOUT_SECONDS
    return value


def _candidate_binary_paths() -> list[Path]:
    candidates: list[Path] = []
    override = (os.environ.get("LAST30DAYS_RUST_BIN") or "").strip()
    if override:
        candidates.append(Path(override).expanduser())
    candidates.append(Path.home() / ".local" / "share" / "last30days" / "bin" / _BINARY_NAME)
    # Dev checkout layout: skills/last30days/scripts/lib/rust_accel.py -> repo root -> rust/target/release/
    repo_root = Path(__file__).resolve().parents[4]
    candidates.append(repo_root / "rust" / "target" / "release" / _BINARY_NAME)
    found_on_path = shutil.which(_BINARY_NAME)
    if found_on_path:
        candidates.append(Path(found_on_path))
    return candidates


def find_binary() -> str | None:
    """Return path to an executable ``last30days-core`` binary, or ``None``."""
    for path in _candidate_binary_paths():
        try:
            if path.is_file() and os.access(path, os.X_OK):
                return str(path)
        except OSError:
            continue
    return None


class _WorkerPool:
    """Process-local, thread-safe persistent stdio worker with bounded I/O."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._proc: subprocess.Popen[bytes] | None = None
        self._bin_path: str | None = None
        self._pid: int = os.getpid()
        self._disabled_for_bin: str | None = None
        self._read_buf = bytearray()

    @staticmethod
    def _terminate_proc(
        proc: subprocess.Popen[bytes] | None,
        *,
        force: bool = False,
    ) -> None:
        if proc is None:
            return
        for stream in (proc.stdin, proc.stdout):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass
        if force:
            try:
                proc.kill()
                proc.wait(timeout=0.5)
            except (OSError, subprocess.TimeoutExpired):
                pass
            return
        try:
            proc.terminate()
            proc.wait(timeout=0.5)
        except (OSError, subprocess.TimeoutExpired):
            try:
                proc.kill()
                proc.wait(timeout=0.5)
            except (OSError, subprocess.TimeoutExpired):
                pass

    def _reset_locked(self, *, force: bool = False) -> None:
        proc = self._proc
        self._proc = None
        self._bin_path = None
        self._read_buf.clear()
        self._terminate_proc(proc, force=force)

    def shutdown(self) -> None:
        acquired = self._lock.acquire(timeout=0.2)
        if acquired:
            try:
                self._reset_locked()
            finally:
                self._lock.release()
        else:
            # Another thread is mid-roundtrip; kill the worker directly so
            # interpreter exit never deadlocks waiting for self._lock.
            self._terminate_proc(self._proc, force=True)

    def _ensure_started_locked(
        self,
        bin_path: str,
        timeout: float,
    ) -> subprocess.Popen[bytes]:
        current_pid = os.getpid()
        if self._pid != current_pid:
            # Forked child: drop parent handle without touching parent's child process.
            self._proc = None
            self._bin_path = None
            self._pid = current_pid
            self._disabled_for_bin = None
            self._read_buf.clear()

        if self._proc is not None and self._proc.poll() is None and self._bin_path == bin_path:
            return self._proc

        self._reset_locked()
        proc = subprocess.Popen(
            [bin_path, "serve"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )
        if os.name == "posix":
            assert proc.stdin is not None and proc.stdout is not None
            os.set_blocking(proc.stdin.fileno(), False)
            os.set_blocking(proc.stdout.fileno(), False)
        self._proc = proc
        self._bin_path = bin_path

        # Handshake once on worker start to confirm protocol compatibility.
        resp = self._roundtrip_locked(proc, {"op": "ping"}, timeout=min(5.0, timeout))
        if resp.get("status") != "ok" or resp.get("protocol") != PROTOCOL_VERSION:
            self._reset_locked()
            raise RustAccelError(
                f"incompatible last30days-core binary at {bin_path}: {resp!r}"
            )
        return proc

    def _roundtrip_posix_locked(
        self,
        proc: subprocess.Popen[bytes],
        data: bytes,
        timeout: float,
    ) -> dict[str, Any]:
        assert proc.stdin is not None and proc.stdout is not None
        deadline = time.monotonic() + timeout
        stdin_fd = proc.stdin.fileno()
        stdout_fd = proc.stdout.fileno()

        view = memoryview(data)
        written = 0
        while written < len(data):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    f"last30days-core worker timed out after {timeout:.1f}s while writing request"
                )
            _, writable, _ = select.select([], [stdin_fd], [], remaining)
            if not writable:
                raise TimeoutError(
                    f"last30days-core worker timed out after {timeout:.1f}s while writing request"
                )
            try:
                count = os.write(stdin_fd, view[written:])
            except BlockingIOError:
                continue
            if count == 0:
                raise RustAccelError("last30days-core worker closed stdin unexpectedly")
            written += count

        while True:
            newline_idx = self._read_buf.find(b"\n")
            if newline_idx != -1:
                raw_line = bytes(self._read_buf[:newline_idx])
                del self._read_buf[: newline_idx + 1]
                return json.loads(raw_line.decode("utf-8"))
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    f"last30days-core worker timed out after {timeout:.1f}s waiting for response"
                )
            readable, _, _ = select.select([stdout_fd], [], [], remaining)
            if not readable:
                raise TimeoutError(
                    f"last30days-core worker timed out after {timeout:.1f}s waiting for response"
                )
            try:
                chunk = os.read(stdout_fd, 65536)
            except BlockingIOError:
                continue
            if not chunk:
                raise RustAccelError("last30days-core worker closed stdout unexpectedly")
            self._read_buf.extend(chunk)

    def _roundtrip_threaded_locked(
        self,
        proc: subprocess.Popen[bytes],
        data: bytes,
        timeout: float,
    ) -> dict[str, Any]:
        assert proc.stdin is not None and proc.stdout is not None
        result: dict[str, Any] = {}
        error: list[BaseException] = []

        def _worker() -> None:
            try:
                assert proc.stdin is not None and proc.stdout is not None
                view = memoryview(data)
                written = 0
                while written < len(data):
                    count = proc.stdin.write(view[written:])
                    if not count:
                        raise RustAccelError("last30days-core worker closed stdin unexpectedly")
                    written += count
                while True:
                    newline_idx = self._read_buf.find(b"\n")
                    if newline_idx != -1:
                        raw_line = bytes(self._read_buf[:newline_idx])
                        del self._read_buf[: newline_idx + 1]
                        result["resp"] = json.loads(raw_line.decode("utf-8"))
                        return
                    chunk = proc.stdout.read(65536)
                    if not chunk:
                        raise RustAccelError("last30days-core worker closed stdout unexpectedly")
                    self._read_buf.extend(chunk)
            except BaseException as exc:  # noqa: BLE001
                error.append(exc)

        thread = threading.Thread(target=_worker, daemon=True)
        thread.start()
        thread.join(timeout=timeout)
        if thread.is_alive():
            self._terminate_proc(proc, force=True)
            thread.join(timeout=0.5)
            raise TimeoutError(
                f"last30days-core worker timed out after {timeout:.1f}s waiting for response"
            )
        if error:
            raise error[0]
        return result["resp"]

    def _roundtrip_locked(
        self,
        proc: subprocess.Popen[bytes],
        payload: dict[str, Any],
        *,
        timeout: float,
    ) -> dict[str, Any]:
        encoded = (
            json.dumps(payload, separators=(",", ":"), ensure_ascii=False) + "\n"
        ).encode("utf-8")
        if os.name == "posix":
            return self._roundtrip_posix_locked(proc, encoded, timeout)
        return self._roundtrip_threaded_locked(proc, encoded, timeout)

    def call(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        mode = engine_mode()
        if mode == "python":
            return None

        bin_path = find_binary()
        if bin_path is None:
            if mode == "rust":
                raise RustAccelError(
                    "LAST30DAYS_ENGINE=rust requested, but no last30days-core binary was found"
                )
            return None

        timeout = worker_timeout()
        with self._lock:
            if mode == "auto" and self._disabled_for_bin == bin_path:
                return None
            try:
                proc = self._ensure_started_locked(bin_path, timeout)
                resp = self._roundtrip_locked(proc, payload, timeout=timeout)
            except TimeoutError as exc:
                self._reset_locked(force=True)
                if mode == "rust":
                    raise RustAccelError(f"last30days-core call failed: {exc}") from exc
                self._disabled_for_bin = bin_path
                return None
            except (OSError, ValueError, RustAccelError) as exc:
                self._reset_locked()
                if mode == "rust":
                    raise RustAccelError(f"last30days-core call failed: {exc}") from exc
                self._disabled_for_bin = bin_path
                return None

        if resp.get("status") != "ok":
            if mode == "rust":
                raise RustAccelError(f"last30days-core error: {resp.get('message')}")
            return None
        return resp


_POOL = _WorkerPool()
atexit.register(_POOL.shutdown)


def shutdown() -> None:
    """Stop any running background ``last30days-core`` worker."""
    _POOL.shutdown()


def is_available() -> bool:
    """Return ``True`` if Rust acceleration is active and reachable."""
    if engine_mode() == "python":
        return False
    return _POOL.call({"op": "ping"}) is not None


def dedupe_kept_indices(
    entries: list[dict[str, str]],
    threshold: float,
) -> list[int] | None:
    """Run pass-2 fuzzy near-duplicate filtering in Rust, returning kept indices."""
    resp = _POOL.call(
        {
            "op": "dedupe_indices",
            "entries": entries,
            "threshold": threshold,
        }
    )
    if resp is None:
        return None
    return resp["indices"]


def annotate_stream(
    items: list[dict[str, Any]],
    ranking_query: str,
    freshness_mode: str,
    reference_date: str,
    max_days: int,
) -> list[dict[str, Any]] | None:
    """Run batch relevance, freshness, and engagement annotation in Rust."""
    resp = _POOL.call(
        {
            "op": "annotate_stream",
            "items": items,
            "ranking_query": ranking_query,
            "freshness_mode": freshness_mode,
            "reference_date": reference_date,
            "max_days": max_days,
        }
    )
    if resp is None:
        return None
    return resp["items"]


def cluster_candidates(
    candidates: list[dict[str, Any]],
    plan: dict[str, str],
) -> list[dict[str, Any]] | None:
    """Run greedy clustering, MMR representative selection, and entity merging in Rust."""
    resp = _POOL.call(
        {
            "op": "cluster_candidates",
            "candidates": candidates,
            "plan": plan,
        }
    )
    if resp is None:
        return None
    return resp["clusters"]


def public_markdown(markdown: str) -> str | None:
    """Run ``library.public_markdown`` privacy redaction in Rust."""
    resp = _POOL.call({"op": "public_markdown", "markdown": markdown})
    if resp is None:
        return None
    return resp["markdown"]


def has_private_corpus(markdown: str) -> bool | None:
    """Run ``library.has_private_corpus`` detection in Rust."""
    resp = _POOL.call({"op": "has_private_corpus", "markdown": markdown})
    if resp is None:
        return None
    return bool(resp["value"])


def is_unbounded_private_brief(
    markdown: str,
    source_format: str | None = None,
) -> bool | None:
    """Run ``library.is_unbounded_private_brief`` check in Rust."""
    resp = _POOL.call(
        {
            "op": "is_unbounded_private_brief",
            "markdown": markdown,
            "source_format": source_format,
        }
    )
    if resp is None:
        return None
    return bool(resp["value"])


def sanitize_markdown_batch(items: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    """Sanitize and fingerprint a batch of Markdown strings in parallel in Rust."""
    resp = _POOL.call({"op": "sanitize_markdown_batch", "items": items})
    if resp is None:
        return None
    return resp["results"]


def scan_markdown_files(requests: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    """Read, sanitize, parse metadata, and fingerprint saved Markdown files in parallel."""
    resp = _POOL.call({"op": "scan_markdown_files", "requests": requests})
    if resp is None:
        return None
    return resp["outcomes"]
