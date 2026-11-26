"""Caption-free transcription: compress -> chunk -> provider-fallback.

When a video/audio item has no captions, this turns the media into text via a
Whisper API. Source-agnostic: the input is a media URL or local path, the output
is transcript text. The pipeline:

  1. Acquire audio (yt-dlp for a URL, or use a local file as-is).
  2. Re-encode to a low-bitrate mono stream so most clips fit under the provider
     upload limit.
  3. If still over the limit, split into bounded-duration chunks.
  4. Transcribe each chunk through an ordered provider list (Groq free tier
     first, OpenAI paid backstop), with per-chunk fallback, then join.

Never raises. Returns a typed :class:`TranscriptResult`; missing prerequisites
(no ffmpeg, no provider key) yield a degraded result with a reason, consumed by
the source-health layer, rather than a crash.
"""

from __future__ import annotations

import os
import re
import secrets
import selectors
import shutil
import socket
import tempfile
import threading
from dataclasses import dataclass, field
from typing import Callable, Optional
from urllib.parse import urlsplit

from . import auth_safety, env, health, http, log, public_host, subproc, usage

# Whisper's documented upload ceiling. We compress to stay under it and chunk
# when a single clip still exceeds it.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_DOWNLOAD_BYTES = 200 * 1024 * 1024
MAX_DOWNLOAD_SIZE_ARG = "200M"
CHUNK_SECONDS = 600  # 10-minute chunks when splitting is required
_PROXY_MAX_HEADER_BYTES = 64 * 1024
_PROXY_MAX_REQUEST_BODY_BYTES = 1024 * 1024
_PROXY_CHUNK_BYTES = 65536
_HTTP_METHOD_RE = re.compile(r"^[A-Z]+$")
_HOP_BY_HOP_HEADERS = frozenset({
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "proxy-connection",
    "te",
    "trailers",
    "upgrade",
})

_PROVIDER_ENDPOINTS = {
    "groq": "https://api.groq.com/openai/v1/audio/transcriptions",
    "openai": "https://api.openai.com/v1/audio/transcriptions",
}
_PROVIDER_MODELS = {
    "groq": "whisper-large-v3",
    "openai": "whisper-1",
}


@dataclass
class TranscriptResult:
    text: str = ""
    ok: bool = False
    reason: str = ""
    provider: str = ""
    chunks: int = 0
    health: Optional[health.SourceHealth] = field(default=None)


def is_available(config: dict) -> bool:
    """True when ffmpeg is present AND at least one Whisper provider key is set."""
    return bool(shutil.which("ffmpeg")) and bool(env.transcription_providers(config))


def transcribe_media(
    source: str,
    config: dict,
    timeout: float = 120.0,
) -> TranscriptResult:
    """Transcribe a media URL or local path. Never raises.

    Returns a degraded result (ok=False, reason set) when prerequisites are
    missing or every provider fails, so callers can report the gap honestly.
    """
    if not shutil.which("ffmpeg"):
        return _degraded("ffmpeg not installed", health.MISSING)
    providers = env.transcription_providers(config)
    if not providers:
        return _degraded(
            "no transcription provider key (set GROQ_API_KEY or OPENAI_API_KEY)",
            health.MISSING,
        )

    workdir = tempfile.mkdtemp(prefix="l30d-transcribe-")
    try:
        audio_path = _acquire_audio(source, workdir, timeout=timeout)
        if not audio_path:
            return _degraded("could not acquire/compress audio", health.ERROR)

        chunk_paths = _chunk_audio(audio_path, workdir)
        if not chunk_paths:
            return _degraded("audio chunking produced no segments", health.ERROR)

        texts: list[str] = []
        used_provider = ""
        for chunk in chunk_paths:
            chunk_text, provider = _transcribe_chunk(chunk, providers, timeout=timeout)
            if chunk_text is None:
                return _degraded(
                    f"all providers failed on a chunk ({len(texts)}/{len(chunk_paths)} done)",
                    health.ERROR,
                )
            texts.append(chunk_text)
            used_provider = provider or used_provider

        joined = "\n".join(t.strip() for t in texts if t.strip())
        if not joined:
            return _degraded("transcription produced empty text", health.DEGRADED)
        return TranscriptResult(
            text=joined,
            ok=True,
            provider=used_provider,
            chunks=len(chunk_paths),
            health=health.SourceHealth(name="transcribe", state=health.OK),
        )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _degraded(reason: str, state: str) -> TranscriptResult:
    return TranscriptResult(
        ok=False,
        reason=reason,
        health=health.SourceHealth(name="transcribe", state=state, reason=reason),
    )


_ASF_GUID = b"\x30\x26\xb2\x75\x8e\x66\xcf\x11\xa6\xd9\x00\xaa\x00\x62\xce\x6c"


def _detect_safe_audio_demuxer(path: str) -> Optional[str]:
    """Identify an explicit self-contained ffmpeg demuxer from magic bytes, or None.

    Rejects playlists/manifests (HLS #EXTM3U, ffconcat, DASH XML, SDP) and any
    unrecognized file so ffmpeg cannot follow external local file references.
    """
    try:
        with open(path, "rb") as fh:
            header = fh.read(32)
    except OSError:
        return None
    if len(header) < 4:
        return None
    if len(header) >= 12 and header[4:8] == b"ftyp":
        return "mov,mp4,m4a,3gp,3g2,mj2"
    if header.startswith(b"\x1a\x45\xdf\xa3"):
        return "matroska,webm"
    if header.startswith(b"OggS"):
        return "ogg"
    if header.startswith(b"fLaC"):
        return "flac"
    if len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WAVE":
        return "wav"
    if len(header) >= 12 and header[:4] == b"FORM" and header[8:12] in (b"AIFF", b"AIFC"):
        return "aiff"
    if header.startswith(b"ID3"):
        return "mp3"
    if header[0] == 0xFF and (header[1] & 0xF6) == 0xF0:
        return "aac"
    if header[0] == 0xFF and (header[1] & 0xE0) == 0xE0 and (header[1] & 0x06) != 0x00:
        return "mp3"
    if header.startswith(_ASF_GUID):
        return "asf"
    return None


def _is_safe_remote_media_url(source: str) -> bool:
    """Return True only for http(s) URLs with valid public host syntax.

    DNS resolution is deferred to ``_PublicMediaProxy`` so slow DNS remains
    inside the downloader's timeout budget and every hop is pinned at connect time.
    """
    if not isinstance(source, str) or not source or source.startswith("-"):
        return False
    try:
        parts = urlsplit(source)
    except ValueError:
        return False
    if "\\" in parts.netloc:
        return False
    return public_host.is_public_address_syntax(source)


def _sanitized_ytdlp_env(proxy_url: str) -> dict[str, str]:
    """Pin proxy env vars and clear NO_PROXY so yt-dlp cannot bypass the validating proxy."""
    clean = {
        k: v
        for k, v in os.environ.items()
        if k.lower() not in ("http_proxy", "https_proxy", "all_proxy", "no_proxy")
    }
    clean["HTTP_PROXY"] = proxy_url
    clean["HTTPS_PROXY"] = proxy_url
    clean["http_proxy"] = proxy_url
    clean["https_proxy"] = proxy_url
    clean["NO_PROXY"] = ""
    clean["no_proxy"] = ""
    return clean


class _PublicMediaProxy:
    """Loopback HTTP/CONNECT proxy enforcing public-IP pinning and mid-transfer byte caps.

    Every yt-dlp hop — initial URL, 3xx redirects, HLS/DASH manifests and segments,
    and HTTPS CONNECT tunnels — resolves fresh and connects only to verified public IPs,
    closing active sockets immediately once cumulative downstream bytes exceed ``max_bytes``.
    """

    def __init__(
        self,
        *,
        max_bytes: int = MAX_DOWNLOAD_BYTES,
        connect_timeout: float = 15.0,
        io_timeout: float = 60.0,
        resolver: Optional[Callable[[str, int], list[tuple]]] = None,
    ) -> None:
        self._max_bytes = max_bytes
        self._connect_timeout = connect_timeout
        self._io_timeout = max(connect_timeout, io_timeout)
        self._resolver = resolver or public_host.resolve_public_addresses
        self._lock = threading.Lock()
        self._bytes_transferred = 0
        self._blocked_count = 0
        self._limit_exceeded = False
        self._closed = False
        self._active_sockets: set[socket.socket] = set()
        self._server: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self.port: int = 0

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def limit_exceeded(self) -> bool:
        with self._lock:
            return self._limit_exceeded

    @property
    def blocked_count(self) -> int:
        with self._lock:
            return self._blocked_count

    def _note_blocked(self) -> None:
        with self._lock:
            self._blocked_count += 1

    def __enter__(self) -> "_PublicMediaProxy":
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        srv.listen(32)
        srv.settimeout(0.2)
        self._server = srv
        self.port = srv.getsockname()[1]
        self._thread = threading.Thread(
            target=self._accept_loop, name="l30d-media-proxy", daemon=True,
        )
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        with self._lock:
            self._closed = True
            sockets = list(self._active_sockets)
            self._active_sockets.clear()
        srv = self._server
        self._server = None
        if srv is not None:
            try:
                srv.close()
            except OSError:
                pass
        for sock in sockets:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def _track_socket(self, sock: socket.socket) -> bool:
        with self._lock:
            if self._closed:
                return False
            self._active_sockets.add(sock)
            return True

    def _untrack_socket(self, sock: socket.socket) -> None:
        with self._lock:
            self._active_sockets.discard(sock)
        try:
            sock.close()
        except OSError:
            pass

    def _abort_all_connections(self) -> None:
        with self._lock:
            sockets = list(self._active_sockets)
        for sock in sockets:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def _account_bytes(self, count: int) -> bool:
        if count <= 0:
            return True
        with self._lock:
            if self._closed or self._limit_exceeded:
                return False
            self._bytes_transferred += count
            if self._bytes_transferred > self._max_bytes:
                self._limit_exceeded = True
                return False
            return True

    def _accept_loop(self) -> None:
        while True:
            srv = self._server
            if srv is None:
                return
            try:
                client, _addr = srv.accept()
            except socket.timeout:
                with self._lock:
                    if self._closed:
                        return
                continue
            except OSError:
                return
            if not self._track_socket(client):
                try:
                    client.close()
                except OSError:
                    pass
                return
            threading.Thread(
                target=self._handle_client, args=(client,), daemon=True,
            ).start()

    def _connect_public(self, host: str, port: int) -> socket.socket:
        if (
            not host
            or not isinstance(port, int)
            or port < 1
            or port > 65535
            or "\\" in host
            or any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in host)
        ):
            raise public_host.NonPublicAddressError("refused invalid proxy destination")
        verified = self._resolver(host, port)
        last_err: Optional[OSError] = None
        for af, socktype, proto, _canonname, sockaddr in verified:
            sock: Optional[socket.socket] = None
            try:
                sock = socket.socket(af, socktype, proto)
                sock.settimeout(self._connect_timeout)
                sock.connect(sockaddr)
                sock.settimeout(self._io_timeout)
                if not self._track_socket(sock):
                    sock.close()
                    raise OSError("proxy closed")
                return sock
            except OSError as exc:
                last_err = exc
                if sock is not None:
                    try:
                        sock.close()
                    except OSError:
                        pass
        if last_err is not None:
            raise last_err
        raise public_host.NonPublicAddressError("refused non-public address")

    @staticmethod
    def _send_error(client: socket.socket, status: str) -> None:
        try:
            client.sendall(f"HTTP/1.1 {status}\r\nConnection: close\r\nContent-Length: 0\r\n\r\n".encode("ascii"))
        except OSError:
            pass

    def _handle_client(self, client: socket.socket) -> None:
        try:
            client.settimeout(self._connect_timeout)
            buf = bytearray()
            while b"\r\n\r\n" not in buf:
                chunk = client.recv(4096)
                if not chunk:
                    return
                buf.extend(chunk)
                if len(buf) > _PROXY_MAX_HEADER_BYTES:
                    self._send_error(client, "431 Request Header Fields Too Large")
                    return
            header_end = buf.index(b"\r\n\r\n")
            header_blob = bytes(buf[:header_end])
            remainder = bytes(buf[header_end + 4:])

            lines = header_blob.split(b"\r\n")
            if not lines:
                self._send_error(client, "400 Bad Request")
                return
            try:
                req_line = lines[0].decode("latin-1")
            except UnicodeDecodeError:
                self._send_error(client, "400 Bad Request")
                return
            parts = req_line.split(" ")
            if len(parts) != 3:
                self._send_error(client, "400 Bad Request")
                return
            method, target, version = parts
            if not _HTTP_METHOD_RE.fullmatch(method) or not version.startswith("HTTP/"):
                self._send_error(client, "400 Bad Request")
                return

            if self.limit_exceeded:
                self._send_error(client, "413 Payload Too Large")
                return

            if method == "CONNECT":
                self._handle_connect(client, target, remainder)
            else:
                self._handle_http(client, method, target, lines[1:], remainder)
        except OSError:
            pass
        finally:
            self._untrack_socket(client)

    def _handle_connect(
        self,
        client: socket.socket,
        target: str,
        remainder: bytes,
    ) -> None:
        if "\\" in target or any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in target):
            self._send_error(client, "400 Bad Request")
            return
        try:
            parsed = urlsplit(f"//{target}")
            port = parsed.port
        except ValueError:
            self._send_error(client, "400 Bad Request")
            return
        if (
            not parsed.hostname
            or port is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            self._send_error(client, "400 Bad Request")
            return
        try:
            upstream = self._connect_public(parsed.hostname, port)
        except (public_host.NonPublicAddressError, ValueError):
            self._note_blocked()
            self._send_error(client, "403 Forbidden")
            return
        except OSError:
            self._send_error(client, "502 Bad Gateway")
            return

        try:
            client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            if remainder:
                upstream.sendall(remainder)

            sel = selectors.DefaultSelector()
            try:
                sel.register(client, selectors.EVENT_READ, "client")
                sel.register(upstream, selectors.EVENT_READ, "upstream")
                client_eof = False
                upstream_eof = False
                while not (client_eof and upstream_eof):
                    with self._lock:
                        if self._closed or self._limit_exceeded:
                            break
                    events = sel.select(timeout=0.5)
                    for key, _mask in events:
                        if key.data == "client":
                            data = client.recv(_PROXY_CHUNK_BYTES)
                            if not data:
                                client_eof = True
                                sel.unregister(client)
                                try:
                                    upstream.shutdown(socket.SHUT_WR)
                                except OSError:
                                    pass
                            else:
                                upstream.sendall(data)
                        else:
                            data = upstream.recv(_PROXY_CHUNK_BYTES)
                            if not data:
                                upstream_eof = True
                                sel.unregister(upstream)
                                try:
                                    client.shutdown(socket.SHUT_WR)
                                except OSError:
                                    pass
                            else:
                                if not self._account_bytes(len(data)):
                                    self._abort_all_connections()
                                    return
                                client.sendall(data)
            finally:
                sel.close()
        finally:
            self._untrack_socket(upstream)

    def _handle_http(
        self,
        client: socket.socket,
        method: str,
        target: str,
        raw_headers: list[bytes],
        remainder: bytes,
    ) -> None:
        if any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in target):
            self._send_error(client, "400 Bad Request")
            return
        try:
            parsed = urlsplit(target)
            port = parsed.port
        except ValueError:
            self._send_error(client, "400 Bad Request")
            return
        if (
            parsed.scheme.lower() != "http"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or "\\" in parsed.netloc
        ):
            self._note_blocked()
            self._send_error(client, "403 Forbidden")
            return
        effective_port = port if port is not None else 80
        try:
            upstream = self._connect_public(parsed.hostname, effective_port)
        except (public_host.NonPublicAddressError, ValueError):
            self._note_blocked()
            self._send_error(client, "403 Forbidden")
            return
        except OSError:
            self._send_error(client, "502 Bad Gateway")
            return

        try:
            forwarded_headers: list[tuple[str, str]] = []
            has_host = False
            content_length = 0
            for line in raw_headers:
                if not line or b":" not in line:
                    continue
                name_b, val_b = line.split(b":", 1)
                name = name_b.decode("latin-1").strip()
                val = val_b.decode("latin-1").strip()
                lower = name.lower()
                if lower in _HOP_BY_HOP_HEADERS:
                    continue
                if lower == "transfer-encoding":
                    self._send_error(client, "501 Not Implemented")
                    return
                if lower == "content-length":
                    try:
                        content_length = int(val)
                    except ValueError:
                        self._send_error(client, "400 Bad Request")
                        return
                    if content_length < 0 or content_length > _PROXY_MAX_REQUEST_BODY_BYTES:
                        self._send_error(client, "413 Payload Too Large")
                        return
                if lower == "host":
                    has_host = True
                forwarded_headers.append((name, val))
            if not has_host:
                forwarded_headers.insert(0, ("Host", parsed.netloc))
            forwarded_headers.append(("Connection", "close"))

            origin_target = (parsed.path or "/") + (f"?{parsed.query}" if parsed.query else "")
            head = [f"{method} {origin_target} HTTP/1.1"]
            head.extend(f"{k}: {v}" for k, v in forwarded_headers)
            upstream.sendall(("\r\n".join(head) + "\r\n\r\n").encode("latin-1"))

            if content_length > 0:
                body_chunks = [remainder[:content_length]]
                remaining = content_length - len(body_chunks[0])
                while remaining > 0:
                    chunk = client.recv(min(_PROXY_CHUNK_BYTES, remaining))
                    if not chunk:
                        break
                    body_chunks.append(chunk)
                    remaining -= len(chunk)
                upstream.sendall(b"".join(body_chunks))

            while True:
                chunk = upstream.recv(_PROXY_CHUNK_BYTES)
                if not chunk:
                    break
                if not self._account_bytes(len(chunk)):
                    self._abort_all_connections()
                    return
                client.sendall(chunk)
        finally:
            self._untrack_socket(upstream)


def _acquire_audio(source: str, workdir: str, timeout: float) -> Optional[str]:
    """Produce a compressed mono/16kHz/low-bitrate audio file, or None.

    For a URL, extract audio with yt-dlp; for a local path, transcode it. The
    re-encode keeps most clips under the upload ceiling.
    """
    if not isinstance(source, str) or not source:
        return None
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in source):
        return None

    abs_workdir = os.path.abspath(workdir)
    try:
        parts = urlsplit(source)
    except ValueError:
        return None
    scheme = parts.scheme.lower()

    demuxer: Optional[str] = None
    if scheme in ("http", "https"):
        if not _is_safe_remote_media_url(source):
            log.debug(f"transcribe: refused unsafe media URL: {source!r}")
            return None
        raw = os.path.join(abs_workdir, "raw.m4a")
        with _PublicMediaProxy(
            max_bytes=MAX_DOWNLOAD_BYTES,
            connect_timeout=min(15.0, max(1.0, float(timeout))),
            io_timeout=max(15.0, float(timeout)),
        ) as media_proxy:
            ok = _run([
                "yt-dlp",
                "--ignore-config",
                "--no-plugin-dirs",
                "--no-cookies-from-browser",
                "--no-playlist",
                "--no-live-from-start",
                "--match-filter", "!is_live",
                "--max-filesize", MAX_DOWNLOAD_SIZE_ARG,
                "--downloader", "dash,m3u8:native",
                "--proxy", media_proxy.url,
                "-f", "bestaudio[protocol*=http]/bestaudio[protocol^=m3u8]",
                "-o", raw,
                "--",
                source,
            ], timeout=timeout, env=_sanitized_ytdlp_env(media_proxy.url))
            if not ok or media_proxy.limit_exceeded:
                return None
        if not os.path.isfile(raw):
            return None
        try:
            if os.path.getsize(raw) > MAX_DOWNLOAD_BYTES:
                return None
        except OSError:
            return None
        # Require an explicit self-contained container format on remote downloads
        # so ffmpeg cannot parse a downloaded HLS/ffconcat playlist and read local files.
        demuxer = _detect_safe_audio_demuxer(raw)
        if demuxer is None:
            log.debug("transcribe: refused downloaded media with unrecognized or playlist format")
            return None
    elif "://" in source or (scheme and len(scheme) > 1):
        # Reject non-http(s) URI schemes and ffmpeg protocol prefixes (file://, ftp://, concat:, etc.).
        log.debug(f"transcribe: refused unsupported media scheme: {source!r}")
        return None
    else:
        raw = os.path.abspath(source)
        if not os.path.isfile(raw):
            return None
        demuxer = _detect_safe_audio_demuxer(raw)

    out = os.path.join(abs_workdir, "audio.mp3")
    # Mono, 16kHz, 32kbps keeps speech intelligible while shrinking the file.
    ffmpeg_cmd = [
        "ffmpeg",
        "-nostdin",
        "-y",
        "-protocol_whitelist", "file",
    ]
    if demuxer is not None:
        ffmpeg_cmd.extend(["-f", demuxer])
    ffmpeg_cmd.extend([
        "-i", raw,
        "-vn",
        "-map_metadata", "-1",
        "-ac", "1",
        "-ar", "16000",
        "-b:a", "32k",
        out,
    ])
    if not _run(ffmpeg_cmd, timeout=timeout):
        return None
    return out if os.path.isfile(out) else None


def _chunk_audio(audio_path: str, workdir: str) -> list[str]:
    """Return [audio_path] when small enough, else ffmpeg-segmented chunk paths."""
    try:
        size = os.path.getsize(audio_path)
    except OSError:
        return []
    if size <= MAX_UPLOAD_BYTES:
        return [audio_path]

    abs_workdir = os.path.abspath(workdir)
    abs_audio = os.path.abspath(audio_path)
    pattern = os.path.join(abs_workdir, "chunk_%03d.mp3")
    if not _run([
        "ffmpeg",
        "-nostdin",
        "-y",
        "-protocol_whitelist", "file",
        "-i", abs_audio,
        "-map_metadata", "-1",
        "-f", "segment",
        "-segment_time", str(CHUNK_SECONDS),
        "-c", "copy",
        pattern,
    ]):
        # Fall back to the single (oversized) file; the provider may still accept it.
        return [audio_path]
    chunks = sorted(
        os.path.join(abs_workdir, f) for f in os.listdir(abs_workdir) if f.startswith("chunk_")
    )
    return chunks or [audio_path]


_SAFE_UPLOAD_FILENAME_RE = re.compile(r"^[A-Za-z0-9_-]+\.mp3$")


def _safe_upload_filename(path: str) -> str:
    """Return a strictly sanitized ASCII upload filename for Content-Disposition."""
    name = os.path.basename(path or "")
    if _SAFE_UPLOAD_FILENAME_RE.fullmatch(name):
        return name
    return "audio.mp3"


def _generate_multipart_boundary(audio: bytes) -> str:
    """Generate a per-request CSPRNG multipart boundary absent from ``audio``."""
    for _ in range(16):
        candidate = f"----l30d-{secrets.token_hex(16)}"
        if candidate.encode("ascii") not in audio:
            return candidate
    raise ValueError("Unable to generate collision-free multipart boundary")


def _transcribe_chunk(
    path: str,
    providers: list[tuple[str, str]],
    timeout: float,
) -> tuple[Optional[str], str]:
    """Try each provider in order; return (text, provider) or (None, '')."""
    for name, key in providers:
        try:
            text = _post_audio(name, path, key, timeout=timeout)
        except Exception as exc:  # noqa: BLE001 - any provider failure -> try the next
            # Surface the failing provider + error (e.g. Groq's Cloudflare 403,
            # "error code: 1010") under LAST30DAYS_DEBUG instead of silently
            # degrading to an empty transcript (#983).
            safe_exc = auth_safety.scrub_secrets(repr(exc), (key,))
            log.debug(f"transcribe: provider {name} failed on a chunk: {safe_exc}")
            text = None
        if text is not None:
            return text, name
    return None, ""


def _post_audio(provider: str, path: str, api_key: str, timeout: float) -> Optional[str]:
    """POST one audio file to a Whisper-compatible endpoint; return text or None."""
    import json
    import urllib.request

    clean_key, key_err = auth_safety.normalize_header_token(api_key)
    if key_err or not clean_key:
        raise ValueError(key_err or "Invalid token format")

    endpoint = _PROVIDER_ENDPOINTS[provider]
    model = _PROVIDER_MODELS[provider]
    with open(path, "rb") as fh:
        audio = fh.read()
    boundary = _generate_multipart_boundary(audio)
    filename = _safe_upload_filename(path)

    parts: list[bytes] = []
    parts.append(f"--{boundary}\r\n".encode("ascii"))
    parts.append(b'Content-Disposition: form-data; name="model"\r\n\r\n')
    parts.append(f"{model}\r\n".encode("ascii"))
    parts.append(f"--{boundary}\r\n".encode("ascii"))
    parts.append(
        (
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            "Content-Type: audio/mpeg\r\n\r\n"
        ).encode("ascii")
    )
    parts.append(audio)
    parts.append(f"\r\n--{boundary}--\r\n".encode("ascii"))
    body = b"".join(parts)

    req = urllib.request.Request(
        endpoint,
        data=body,
        headers={
            "Authorization": f"Bearer {clean_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            # A plain-urllib UA is fingerprinted and 403'd by Cloudflare-style
            # edges (Groq returns "error code: 1010", #983). Send the skill's
            # canonical identity instead, same as lib/http.py's get/post.
            "User-Agent": http.USER_AGENT,
        },
        method="POST",
    )
    usage.begin(provider)
    with http.open_request(req, timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data.get("text")


def _run(
    command: list[str],
    timeout: float = 120.0,
    *,
    env: Optional[dict[str, str]] = None,
) -> bool:
    """Run a subprocess; return True on exit 0, False on any failure. No raise.

    Uses subproc.run_with_timeout so a timed-out yt-dlp/ffmpeg is killed at the
    process-group level (os.setsid/killpg) instead of orphaning child trees.
    """
    try:
        kwargs: dict = {"timeout": int(timeout)}
        if env is not None:
            kwargs["env"] = env
        result = subproc.run_with_timeout(command, **kwargs)
    except (subproc.SubprocTimeout, FileNotFoundError, OSError):
        return False
    return result.returncode == 0
