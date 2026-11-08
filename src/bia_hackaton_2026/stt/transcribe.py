"""STT helpers ported from MBAssist-AI speech.py (Sarvam for Malayalam, Groq Whisper for English)."""
import io
import os
import time

import requests
from groq import Groq

from bia_hackaton_2026.llm import usage
from bia_hackaton_2026.llm.groq_chat import key_tag, with_groq_keys

SARVAM_URL = "https://api.sarvam.ai/speech-to-text"

MIME_BY_EXT = {
    "wav": "audio/wav", "mp3": "audio/mpeg", "aac": "audio/aac", "m4a": "audio/mp4",
    "ogg": "audio/ogg", "opus": "audio/ogg", "webm": "audio/webm", "flac": "audio/flac",
    "aiff": "audio/aiff",
}


def guess_mime(filename: str) -> str:
    return MIME_BY_EXT.get(filename.rsplit(".", 1)[-1].lower(), "audio/wav")


SARVAM_SYNC_LIMIT_ERROR = "exceeds the maximum limit"   # Sarvam's real-time endpoint rejects audio over 30 s


def sarvam_batch(file_bytes: bytes, filename: str, mode: str, key: str, duration: float | None = None) -> tuple[str, str]:
    """Transcribe long audio with Sarvam's batch API (a job: create, upload, start, wait, download). Measured on Windows at about
    6 s for a 48 s recording, against about 1 s for the real-time endpoint on short audio. Returns (text, error)."""
    import json
    import tempfile
    from pathlib import Path

    from sarvamai import SarvamAI

    t0 = time.perf_counter()
    secs = duration or len(file_bytes) / 4500
    try:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / (Path(filename).name or "audio.wav")
            src.write_bytes(file_bytes)
            job = SarvamAI(api_subscription_key=key.strip()).speech_to_text_job.create_job(
                model="saaras:v3", mode=mode, language_code="ml-IN", with_diarization=False)
            job.upload_files(file_paths=[str(src)])
            job.start()
            job.wait_until_complete(poll_interval=1, timeout=300)
            res = job.get_file_results()
            if res.get("failed"):
                raise RuntimeError(res["failed"][0].get("error_message") or "batch job failed")
            out = Path(tmp) / "out"
            job.download_outputs(output_dir=str(out))
            text = " ".join(json.loads(p.read_text(encoding="utf-8")).get("transcript", "").strip() for p in sorted(out.glob("*.json"))).strip()
        usage.record("sarvam", "saaras:v3 (batch)", "stt", bool(text), 200, seconds=time.perf_counter() - t0, audio_seconds=secs if text else 0.0)
        return text, "" if text else "Sarvam batch returned an empty transcript."
    except Exception as e:
        usage.record("sarvam", "saaras:v3 (batch)", "stt", False, 0, seconds=time.perf_counter() - t0)
        return "", f"Sarvam batch failed: {e}"


def transcribe_audio(
    file_bytes: bytes,
    filename: str = "audio.wav",
    language: str = "en",
    sarvam_key: str | None = None,
    groq_key: str | None = None,
    duration: float | None = None,
) -> tuple[str, str, str]:
    """language: 'en' -> Groq Whisper, 'ml' -> Sarvam transcribe, 'ml-mix' -> Sarvam codemix.
    Returns (text, provider, error). No cross-language fallback: Malayalam is never sent
    to Whisper (it auto-detects and often mislabels Malayalam as Tamil)."""
    if language in ("ml", "ml-mix"):
        sarvam_key = sarvam_key or os.environ.get("SARVAM_AI")
        if not sarvam_key:
            return "", "none", "Sarvam API key missing (set SARVAM_AI in .env or the sidebar)."
        mode = "codemix" if language == "ml-mix" else "transcribe"
        if duration and duration > 28:   # known to be over the 30 s limit: skip the attempt that would be rejected
            text, err = sarvam_batch(file_bytes, filename, mode, sarvam_key, duration)
            return text, "sarvam", err
        t0 = time.perf_counter()
        secs = duration or len(file_bytes) / 4500   # voice notes are ~4.5 KB/s when the length is not known
        try:
            r = requests.post(
                SARVAM_URL,
                headers={"api-subscription-key": sarvam_key.strip()},
                files={"file": (filename, file_bytes, guess_mime(filename))},
                data={"model": "saaras:v3", "mode": mode, "language_code": "ml-IN"},
                timeout=30,
            )
        except Exception as e:
            usage.record("sarvam", "saaras:v3", "stt", False, 0, seconds=time.perf_counter() - t0)
            return "", "sarvam", f"Sarvam request failed: {e}"
        usage.record("sarvam", "saaras:v3", "stt", r.status_code == 200, r.status_code, seconds=time.perf_counter() - t0,
                     audio_seconds=secs if r.status_code == 200 else 0.0)
        if r.status_code == 400 and SARVAM_SYNC_LIMIT_ERROR in r.text:   # longer than 30 s: use the batch API instead
            text, err = sarvam_batch(file_bytes, filename, mode, sarvam_key, duration)
            return text, "sarvam", err
        if r.status_code != 200:
            return "", "sarvam", f"Sarvam returned {r.status_code}: {r.text[:300]}"
        text = r.json().get("transcript", "").strip()
        if not text:
            return "", "sarvam", "Sarvam returned an empty transcript."
        return text, "sarvam", ""

    groq_key = groq_key or os.environ.get("GROQ_API_KEY")
    if not groq_key:
        return "", "none", "Groq API key missing (set GROQ_API_KEY in .env or the sidebar)."
    t0 = time.perf_counter()
    secs = duration or len(file_bytes) / 4500
    try:
        def _call(client):
            buf = io.BytesIO(file_bytes)
            buf.name = filename
            return client.audio.transcriptions.create(file=(filename, buf), model="whisper-large-v3", language="en", response_format="json")
        t = with_groq_keys(_call, groq_key, "whisper", model="whisper-large-v3")
        usage.record("groq", "whisper-large-v3" + key_tag(), "stt", True, 200, seconds=time.perf_counter() - t0, audio_seconds=secs)
        return t.text.strip(), "groq", ""
    except Exception as e:
        usage.record("groq", "whisper-large-v3", "stt", False, 0, seconds=time.perf_counter() - t0)
        return "", "groq", f"Groq Whisper failed: {e}"


def trim_head(file_bytes: bytes, seconds: float = 15.0) -> bytes:
    """First `seconds` of an audio file as WAV bytes (wav, ogg/opus, flac, mp3 via libsndfile). Returns the original bytes if the
    format cannot be read (for example browser webm), which only makes the language check slower, not wrong."""
    try:
        import soundfile as sf
        data, sr = sf.read(io.BytesIO(file_bytes), frames=int(seconds * 48000), dtype="float32")
        # frames= counts samples at the file's own rate, so cut again by time
        data = data[: int(seconds * sr)]
        out = io.BytesIO()
        sf.write(out, data, sr, format="WAV")
        return out.getvalue()
    except Exception:
        return file_bytes


def whisper_detect(file_bytes: bytes, filename: str = "audio.wav", groq_key: str | None = None,
                   duration: float | None = None) -> tuple[str, str, str]:
    """Free language check + transcript in one Groq Whisper call (language not pinned).
    Returns (language, text, error); language is Whisper's name for it, lower case ("english", "tamil", ...).
    Only "english" is trusted: Whisper often labels Malayalam as Tamil or another Indian language and its Malayalam text is
    unreliable, but on English speech it is accurate, so the caller uses it only to decide "English or not"."""
    groq_key = groq_key or os.environ.get("GROQ_API_KEY")
    if not groq_key:
        return "", "", "Groq API key missing (set GROQ_API_KEY in .env or the sidebar)."
    t0 = time.perf_counter()
    secs = duration or len(file_bytes) / 4500
    try:
        def _call(client):
            buf = io.BytesIO(file_bytes)
            buf.name = filename
            return client.audio.transcriptions.create(file=(filename, buf), model="whisper-large-v3", response_format="verbose_json")
        t = with_groq_keys(_call, groq_key, "whisper", model="whisper-large-v3")
        usage.record("groq", "whisper-large-v3" + key_tag(), "stt", True, 200, seconds=time.perf_counter() - t0, audio_seconds=secs)
        return (getattr(t, "language", "") or "").strip().lower(), (t.text or "").strip(), ""
    except Exception as e:
        usage.record("groq", "whisper-large-v3", "stt", False, 0, seconds=time.perf_counter() - t0)
        return "", "", f"Groq Whisper failed: {e}"
