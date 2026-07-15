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
import shutil
import tempfile
from dataclasses import dataclass, field
from typing import Optional

from . import env, health, subproc

# Whisper's documented upload ceiling. We compress to stay under it and chunk
# when a single clip still exceeds it.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
CHUNK_SECONDS = 600  # 10-minute chunks when splitting is required

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


def _acquire_audio(source: str, workdir: str, timeout: float) -> Optional[str]:
    """Produce a compressed mono/16kHz/low-bitrate audio file, or None.

    For a URL, extract audio with yt-dlp; for a local path, transcode it. The
    re-encode keeps most clips under the upload ceiling.
    """
    raw = source
    if source.startswith("http"):
        raw = os.path.join(workdir, "raw.m4a")
        if not _run([
            "yt-dlp", "-f", "bestaudio", "-o", raw, "--no-playlist", source
        ], timeout=timeout):
            return None
        if not os.path.exists(raw):
            return None
    elif not os.path.exists(source):
        return None

    out = os.path.join(workdir, "audio.mp3")
    # Mono, 16kHz, 32kbps keeps speech intelligible while shrinking the file.
    if not _run([
        "ffmpeg", "-y", "-i", raw, "-ac", "1", "-ar", "16000", "-b:a", "32k", out
    ], timeout=timeout):
        return None
    return out if os.path.exists(out) else None


def _chunk_audio(audio_path: str, workdir: str) -> list[str]:
    """Return [audio_path] when small enough, else ffmpeg-segmented chunk paths."""
    try:
        size = os.path.getsize(audio_path)
    except OSError:
        return []
    if size <= MAX_UPLOAD_BYTES:
        return [audio_path]

    pattern = os.path.join(workdir, "chunk_%03d.mp3")
    if not _run([
        "ffmpeg", "-y", "-i", audio_path, "-f", "segment",
        "-segment_time", str(CHUNK_SECONDS), "-c", "copy", pattern
    ]):
        # Fall back to the single (oversized) file; the provider may still accept it.
        return [audio_path]
    chunks = sorted(
        os.path.join(workdir, f) for f in os.listdir(workdir) if f.startswith("chunk_")
    )
    return chunks or [audio_path]


def _transcribe_chunk(
    path: str,
    providers: list[tuple[str, str]],
    timeout: float,
) -> tuple[Optional[str], str]:
    """Try each provider in order; return (text, provider) or (None, '')."""
    for name, key in providers:
        try:
            text = _post_audio(name, path, key, timeout=timeout)
        except Exception:  # noqa: BLE001 - any provider failure -> try the next
            text = None
        if text is not None:
            return text, name
    return None, ""


def _post_audio(provider: str, path: str, api_key: str, timeout: float) -> Optional[str]:
    """POST one audio file to a Whisper-compatible endpoint; return text or None."""
    import json
    import urllib.request

    endpoint = _PROVIDER_ENDPOINTS[provider]
    model = _PROVIDER_MODELS[provider]
    boundary = "----l30dTranscribeBoundary"
    with open(path, "rb") as fh:
        audio = fh.read()

    parts: list[bytes] = []
    parts.append(f"--{boundary}\r\n".encode())
    parts.append(b'Content-Disposition: form-data; name="model"\r\n\r\n')
    parts.append(f"{model}\r\n".encode())
    parts.append(f"--{boundary}\r\n".encode())
    parts.append(
        b'Content-Disposition: form-data; name="file"; filename="audio.mp3"\r\n'
        b"Content-Type: audio/mpeg\r\n\r\n"
    )
    parts.append(audio)
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    body = b"".join(parts)

    req = urllib.request.Request(
        endpoint,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data.get("text")


def _run(command: list[str], timeout: float = 120.0) -> bool:
    """Run a subprocess; return True on exit 0, False on any failure. No raise.

    Uses subproc.run_with_timeout so a timed-out yt-dlp/ffmpeg is killed at the
    process-group level (os.setsid/killpg) instead of orphaning child trees.
    """
    try:
        result = subproc.run_with_timeout(command, timeout=int(timeout))
    except (subproc.SubprocTimeout, FileNotFoundError, OSError):
        return False
    return result.returncode == 0
