"""Native Rust core bridge with verified auto-install and transparent Python fallback.

Discovers or installs a standalone ``last30days-core`` binary (no Rust toolchain
required on the user's machine) and communicates over a persistent
newline-delimited JSON stdio worker. Pure Python remains as an automatic
fallback for offline sandboxes, unsupported architectures, or when
``LAST30DAYS_ENGINE=python`` is set.
"""

from __future__ import annotations

import atexit
import hashlib
import hmac
import http.client
import json
import math
import os
import platform
import re
import select
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from . import skill_meta

PROTOCOL_VERSION = 1
DEFAULT_TIMEOUT_SECONDS = 30.0
RELEASE_REPO = "mvanhorn/last30days-skill"
CHECKSUMS_ASSET = "last30days-core-SHA256SUMS.txt"
MAX_BINARY_BYTES = 25 * 1024 * 1024
MAX_CHECKSUMS_BYTES = 64 * 1024
DOWNLOAD_TIMEOUT_SECONDS = 30.0
AUTO_INSTALL_COOLDOWN_SECONDS = 24 * 60 * 60

_BINARY_NAME = "last30days-core.exe" if sys.platform == "win32" else "last30days-core"
_VERSION_STAMP_NAME = ".last30days-core-version"
_ATTEMPT_STAMP_NAME = ".last30days-core-attempt"
_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
_ALLOWED_DOWNLOAD_HOSTS = frozenset(
    {
        "github.com",
        "objects.githubusercontent.com",
        "release-assets.githubusercontent.com",
    }
)


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


def install_bin_path() -> Path:
    """Return the managed user-local install path for ``last30days-core``."""
    return Path.home() / ".local" / "share" / "last30days" / "bin" / _BINARY_NAME


def _candidate_binary_paths() -> list[Path]:
    candidates: list[Path] = []
    override = (os.environ.get("LAST30DAYS_RUST_BIN") or "").strip()
    if override:
        candidates.append(Path(override).expanduser())
    candidates.append(install_bin_path())
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


def platform_asset_name(
    system: str | None = None,
    machine: str | None = None,
) -> str | None:
    """Return the GitHub Release asset name for this OS/architecture, or ``None``."""
    plat = (system if system is not None else sys.platform).lower()
    arch = (machine if machine is not None else platform.machine()).lower()
    if plat == "darwin":
        if arch in {"arm64", "aarch64"}:
            return "last30days-core-darwin-arm64"
        if arch in {"x86_64", "amd64"}:
            return "last30days-core-darwin-amd64"
        return None
    if plat.startswith("linux"):
        if arch in {"x86_64", "amd64"}:
            return "last30days-core-linux-amd64"
        if arch in {"aarch64", "arm64"}:
            return "last30days-core-linux-arm64"
        return None
    if plat in {"win32", "cygwin", "msys"}:
        if arch in {"amd64", "x86_64"}:
            return "last30days-core-windows-amd64.exe"
        return None
    return None


def expected_skill_version(script_file: Path | None = None) -> str | None:
    """Return validated ``X.Y.Z`` version from skill, plugin, or MCP sentinel metadata."""
    here = (script_file if script_file is not None else Path(__file__)).resolve()
    candidates: list[str | None] = []

    # 1. Standard skill install: <skill>/scripts/lib/rust_accel.py -> <skill>/SKILL.md
    if len(here.parents) > 2:
        candidates.append(skill_meta.read_skill_version(here.parents[2] / "SKILL.md"))

    # 2. Plugin cache install: ancestor .claude-plugin/plugin.json
    for parent in here.parents:
        manifest = parent / ".claude-plugin" / "plugin.json"
        if manifest.is_file():
            try:
                raw_ver = json.loads(manifest.read_text(encoding="utf-8")).get("version")
            except (OSError, ValueError):
                continue
            if isinstance(raw_ver, str):
                candidates.append(raw_ver)
                break

    # 3. Extracted MCP bundle: <cache>/<version>/lib/rust_accel.py -> <cache>/<version>/.version
    if len(here.parents) > 1:
        candidates.append(_read_stamp(here.parents[1] / ".version"))

    for raw in candidates:
        if not raw:
            continue
        cleaned = raw.strip().removeprefix("v")
        if _SEMVER_RE.fullmatch(cleaned):
            return cleaned
    return None


def _validate_download_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or host not in _ALLOWED_DOWNLOAD_HOSTS
    ):
        raise ValueError(f"refusing disallowed download URL: {url}")
    return url


class _SafeHttpsRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Allow only HTTPS redirects to GitHub release asset hosts."""

    max_redirections = 5

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        resolved = urllib.parse.urljoin(req.full_url, newurl)
        _validate_download_url(resolved)
        return super().redirect_request(req, fp, code, msg, headers, resolved)


def _https_fetch_bytes(
    url: str,
    *,
    max_bytes: int,
    timeout: float = DOWNLOAD_TIMEOUT_SECONDS,
) -> bytes:
    """Fetch bounded bytes from an allowlisted GitHub Releases HTTPS URL."""
    _validate_download_url(url)
    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(),
        _SafeHttpsRedirectHandler(),
    )
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "last30days-core-installer/1"},
    )
    with opener.open(req, timeout=timeout) as resp:
        _validate_download_url(resp.geturl())
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = resp.read(65536)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise ValueError(f"download exceeds {max_bytes} byte limit")
            chunks.append(chunk)
    return b"".join(chunks)


def parse_checksums(text: str) -> dict[str, str]:
    """Parse ``sha256sum`` output into ``{asset_filename: sha256_hex}``."""
    out: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        digest, name = parts[0].lower(), parts[1].strip()
        if name.startswith("*"):
            name = name[1:]
        if not _HEX64_RE.fullmatch(digest):
            continue
        if not name or "/" in name or "\\" in name or name in {".", ".."}:
            continue
        out[name] = digest
    return out


def _verify_binary_executable(bin_path: Path) -> tuple[bool, str | None]:
    """Run a one-shot ping against ``bin_path`` to verify execution and protocol."""
    try:
        proc = subprocess.run(
            [str(bin_path), "serve"],
            input='{"op":"ping"}\n',
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=5.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False, None
    if proc.returncode != 0 or not isinstance(proc.stdout, str):
        return False, None
    first_line = proc.stdout.strip().splitlines()[:1]
    if not first_line:
        return False, None
    try:
        resp = json.loads(first_line[0])
    except ValueError:
        return False, None
    if resp.get("status") != "ok" or resp.get("protocol") != PROTOCOL_VERSION:
        return False, None
    version = resp.get("version")
    return True, str(version) if version else None


def _read_stamp(path: Path) -> str | None:
    try:
        if path.is_symlink() or not path.is_file():
            return None
        return path.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def _write_stamp_atomic(path: Path, value: str) -> None:
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as fh:
            tmp_path = Path(fh.name)
            fh.write(value + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, path)
        tmp_path = None
    except OSError:
        pass
    finally:
        if tmp_path is not None:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass


def _ensure_safe_install_dir(dest_dir: Path) -> None:
    """Create ``dest_dir`` safely and reject symlinked install targets."""
    if dest_dir.is_symlink():
        raise OSError(f"refusing to install into symlink directory: {dest_dir}")
    dest_dir.mkdir(parents=True, exist_ok=True)
    if dest_dir.is_symlink() or not dest_dir.is_dir():
        raise OSError(f"install directory is not a regular directory: {dest_dir}")


def _release_asset_url(version: str, asset: str) -> str:
    return f"https://github.com/{RELEASE_REPO}/releases/download/v{version}/{asset}"


def install_release_binary(
    *,
    version: str | None = None,
    force: bool = False,
    timeout: float = DOWNLOAD_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Ensure a verified ``last30days-core`` binary is installed for this platform.

    Downloads the platform release binary and ``last30days-core-SHA256SUMS.txt``
    from GitHub Releases over HTTPS, verifies the SHA-256 digest and live
    protocol handshake before atomically installing into
    ``~/.local/share/last30days/bin/last30days-core``. Never raises.
    """
    if engine_mode() == "python":
        return {
            "installed": False,
            "action": "disabled",
            "path": None,
            "version": None,
            "detail": "LAST30DAYS_ENGINE=python",
        }

    target_version = version if version is not None else expected_skill_version()
    managed_path = install_bin_path()
    version_stamp = managed_path.parent / _VERSION_STAMP_NAME
    attempt_stamp = managed_path.parent / _ATTEMPT_STAMP_NAME
    had_managed = managed_path.is_file() and not managed_path.is_symlink()

    if not force:
        existing = find_binary()
        if existing is not None:
            existing_path = Path(existing)
            is_managed = existing_path == managed_path
            if not is_managed:
                return {
                    "installed": True,
                    "action": "already_installed",
                    "path": existing,
                    "version": None,
                    "detail": "using existing binary",
                }
            had_managed = True
            stamped = _read_stamp(version_stamp)
            if not target_version or stamped == target_version:
                return {
                    "installed": True,
                    "action": "already_installed",
                    "path": existing,
                    "version": stamped,
                    "detail": "managed binary up to date",
                }

    asset = platform_asset_name()
    if asset is None:
        return {
            "installed": had_managed,
            "action": "unsupported_platform",
            "path": str(managed_path) if had_managed else None,
            "version": _read_stamp(version_stamp) if had_managed else None,
            "detail": f"no prebuilt binary for {sys.platform}/{platform.machine()}; using Python fallback",
        }

    if not target_version or not _SEMVER_RE.fullmatch(target_version):
        return {
            "installed": had_managed,
            "action": "install_failed",
            "path": str(managed_path) if had_managed else None,
            "version": _read_stamp(version_stamp) if had_managed else None,
            "detail": f"invalid release version: {target_version!r}",
        }

    tmp_path: Path | None = None
    try:
        checksums_raw = _https_fetch_bytes(
            _release_asset_url(target_version, CHECKSUMS_ASSET),
            max_bytes=MAX_CHECKSUMS_BYTES,
            timeout=timeout,
        )
        checksums = parse_checksums(checksums_raw.decode("utf-8"))
        expected_sha = checksums.get(asset)
        if not expected_sha:
            raise ValueError(f"no SHA-256 checksum published for {asset}")

        binary_bytes = _https_fetch_bytes(
            _release_asset_url(target_version, asset),
            max_bytes=MAX_BINARY_BYTES,
            timeout=timeout,
        )
        actual_sha = hashlib.sha256(binary_bytes).hexdigest()
        if not hmac.compare_digest(actual_sha, expected_sha):
            raise ValueError(
                f"SHA-256 mismatch for {asset} (expected {expected_sha[:12]}..., got {actual_sha[:12]}...)"
            )

        _ensure_safe_install_dir(managed_path.parent)
        if managed_path.is_symlink():
            raise OSError(f"refusing to overwrite symlink binary: {managed_path}")

        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=managed_path.parent,
            prefix=".last30days-core-dl-",
            delete=False,
        ) as fh:
            tmp_path = Path(fh.name)
            fh.write(binary_bytes)
            fh.flush()
            os.fsync(fh.fileno())

        if os.name == "posix":
            os.chmod(tmp_path, stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)

        ok, bin_ver = _verify_binary_executable(tmp_path)
        if not ok:
            raise RustAccelError("downloaded binary failed protocol handshake verification")

        shutdown()
        os.replace(tmp_path, managed_path)
        tmp_path = None
        _write_stamp_atomic(version_stamp, target_version)
        try:
            attempt_stamp.unlink(missing_ok=True)
        except OSError:
            pass
        _POOL._disabled_for_bin = None
        _POOL._verified_bin = str(managed_path)
        _POOL._last_error = None
        return {
            "installed": True,
            "action": "upgraded" if had_managed else "installed",
            "path": str(managed_path),
            "version": target_version or bin_ver,
            "detail": f"verified SHA-256 and installed {asset}",
        }
    except (
        OSError,
        ValueError,
        urllib.error.URLError,
        http.client.HTTPException,
        RustAccelError,
    ) as exc:
        return {
            "installed": had_managed,
            "action": "install_failed",
            "path": str(managed_path) if had_managed else None,
            "version": _read_stamp(version_stamp) if had_managed else None,
            "detail": str(exc),
        }
    finally:
        if tmp_path is not None:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass


def _auto_install_target_version() -> str | None:
    """Return target release version if a live run would attempt auto-install, else ``None``."""
    if engine_mode() == "python":
        return None
    target_version = expected_skill_version()
    managed_path = install_bin_path()
    version_stamp = managed_path.parent / _VERSION_STAMP_NAME
    existing = find_binary()
    if existing is not None:
        if Path(existing) != managed_path:
            return None
        if not target_version or _read_stamp(version_stamp) == target_version:
            return None
    if platform_asset_name() is None or not target_version:
        return None

    attempt_stamp = managed_path.parent / _ATTEMPT_STAMP_NAME
    last_attempt = _read_stamp(attempt_stamp)
    if last_attempt is not None:
        try:
            if (time.time() - float(last_attempt)) < AUTO_INSTALL_COOLDOWN_SECONDS:
                return None
        except ValueError:
            pass
    return target_version


def would_auto_install_on_run() -> bool:
    """Return ``True`` when the next live run would download and install ``last30days-core``."""
    return _auto_install_target_version() is not None


def ensure_installed_for_run(*, timeout: float = 10.0) -> dict[str, Any] | None:
    """Best-effort one-time auto-install on live runs with 24h failure backoff.

    Returns the install result dict when an install/upgrade was attempted, or
    ``None`` when a current binary is already available, Rust is disabled, or a
    recent failed attempt is still within its cooldown window.
    """
    target_version = _auto_install_target_version()
    if target_version is None:
        return None

    managed_path = install_bin_path()
    attempt_stamp = managed_path.parent / _ATTEMPT_STAMP_NAME
    result = install_release_binary(version=target_version, timeout=timeout)
    if result.get("action") == "install_failed":
        try:
            _ensure_safe_install_dir(managed_path.parent)
            _write_stamp_atomic(attempt_stamp, f"{time.time():.0f}")
        except OSError:
            pass
    return result


def status() -> dict[str, Any]:
    """Return offline, zero-subprocess status snapshot for ``doctor``."""
    mode = engine_mode()
    expected = expected_skill_version()
    asset = platform_asset_name()
    if mode == "python":
        return {
            "mode": mode,
            "state": "disabled",
            "active": False,
            "verified": False,
            "binary_path": None,
            "installed_version": None,
            "expected_version": expected,
            "platform_asset": asset,
            "fallback": "python",
            "detail": "LAST30DAYS_ENGINE=python",
        }
    bin_path = find_binary()
    if bin_path is None:
        return {
            "mode": mode,
            "state": "not_installed",
            "active": False,
            "verified": False,
            "binary_path": None,
            "installed_version": None,
            "expected_version": expected,
            "platform_asset": asset,
            "fallback": "python",
            "detail": None,
        }
    path_obj = Path(bin_path)
    stamped = _read_stamp(path_obj.parent / _VERSION_STAMP_NAME) if path_obj == install_bin_path() else None
    with _POOL._lock:
        disabled = mode == "auto" and _POOL._disabled_for_bin == bin_path
        verified_live = _POOL._verified_bin == bin_path
        last_error = _POOL._last_error
    if disabled:
        return {
            "mode": mode,
            "state": "disabled_after_error",
            "active": False,
            "verified": False,
            "binary_path": bin_path,
            "installed_version": stamped,
            "expected_version": expected,
            "platform_asset": asset,
            "fallback": "python",
            "detail": last_error or "worker disabled after runtime failure",
        }
    verified = verified_live or (path_obj == install_bin_path() and stamped is not None)
    return {
        "mode": mode,
        "state": "active" if verified else "discovered_unverified",
        "active": verified,
        "verified": verified,
        "binary_path": bin_path,
        "installed_version": stamped,
        "expected_version": expected,
        "platform_asset": asset,
        "fallback": None if verified else "python (unverified)",
        "detail": None if verified else "binary discovered on disk; protocol not yet verified",
    }


class _WorkerPool:
    """Process-local, thread-safe persistent stdio worker with bounded I/O."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._proc: subprocess.Popen[bytes] | None = None
        self._bin_path: str | None = None
        self._pid: int = os.getpid()
        self._disabled_for_bin: str | None = None
        self._verified_bin: str | None = None
        self._last_error: str | None = None
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
            self._verified_bin = None
            self._last_error = None
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
        self._verified_bin = bin_path
        self._last_error = None
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
                self._verified_bin = None
                self._last_error = str(exc)
                return None
            except (OSError, ValueError, RustAccelError) as exc:
                self._reset_locked()
                if mode == "rust":
                    raise RustAccelError(f"last30days-core call failed: {exc}") from exc
                self._disabled_for_bin = bin_path
                self._verified_bin = None
                self._last_error = str(exc)
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
