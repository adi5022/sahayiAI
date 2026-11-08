"""Language identification for voice messages: English / Malayalam / code-mix.

Approach: transcribe once with Sarvam (codemix mode, handles ml, en and mixed), then classify by
script of the transcript. Whisper's own language detection is not used because it confuses
Malayalam with Tamil. English results are re-transcribed with Groq Whisper (language pinned)."""
import time

from .transcribe import transcribe_audio, trim_head, whisper_detect

MODELS = {"sarvam": "saaras:v3", "groq": "whisper-large-v3"}


def timed_step(label, mode, file_bytes, filename, lang, sarvam_key, groq_key, duration=None):
    """Run one STT call; return (text, provider, error, trace_row)."""
    t0 = time.perf_counter()
    text, provider, err = transcribe_audio(file_bytes, filename, lang, sarvam_key, groq_key, duration)
    row = {"step": label, "provider": provider, "model": MODELS.get(provider, "-"), "mode": mode,
           "seconds": round(time.perf_counter() - t0, 2), "chars": len(text), "ok": bool(text), "error": err}
    return text, provider, err, row

ML_MIN_SHARE = 0.05   # below this share of Malayalam letters -> English
EN_MIN_SHARE = 0.10   # below this share of Latin letters -> Malayalam


def classify_text(text: str) -> tuple[str, float]:
    """Return ('en' | 'ml' | 'ml-mix', malayalam_share) from the script mix of `text`."""
    ml = sum('ഀ' <= c <= 'ൿ' for c in text)
    latin = sum(c.isascii() and c.isalpha() for c in text)
    total = ml + latin
    if total == 0:
        return "unknown", 0.0
    ml_share = ml / total
    if ml_share < ML_MIN_SHARE:
        return "en", ml_share
    if 1 - ml_share < EN_MIN_SHARE:
        return "ml", ml_share
    return "ml-mix", ml_share


def detect_and_transcribe(file_bytes, filename, sarvam_key=None, groq_key=None, duration=None):
    """Returns dict: text, language, provider, ml_share, error, trace (list of step rows), first_pass.

    Free-first routing: one Groq Whisper call listens to the audio. If Whisper says the language is English, its transcript is
    used and Sarvam is never called (no credits). Anything else goes to Sarvam in code-mix mode. Whisper is only trusted for
    "English or not", never for Malayalam. Tested on 22 recordings: all 6 English clips were detected as English, and 10 of 11
    Malayalam clips were not; English-dominant mixed speech can be taken for English, so a language can also be chosen by hand."""
    trace = []
    t0 = time.perf_counter()
    long_audio = (duration or len(file_bytes) / 4500) > 20
    # Long recordings: the language check only needs the first 8 seconds, which is much faster than transcribing all of it
    probe, probe_name = (trim_head(file_bytes, 8.0), "head.wav") if long_audio else (file_bytes, filename)
    wlang, wtext, werr = whisper_detect(probe, probe_name, groq_key, None if long_audio else duration)
    trace.append({"step": "1. Whisper language check", "provider": "groq", "model": MODELS["groq"], "mode": f"detected={wlang or '?'}",
                  "seconds": round(time.perf_counter() - t0, 2), "chars": len(wtext), "ok": bool(wtext), "error": werr})
    if wlang == "english" and wtext and long_audio:   # English: now transcribe the whole recording (free, Whisper only)
        full, fprov, ferr, row = timed_step("2. English transcript", "language=en", file_bytes, filename, "en", sarvam_key, groq_key, duration)
        trace.append(row)
        if full:
            return {"text": full, "language": "en", "provider": fprov, "ml_share": 0.0, "error": "", "trace": trace, "first_pass": wtext}
    elif wlang == "english" and wtext:
        return {"text": wtext, "language": "en", "provider": "groq", "ml_share": 0.0, "error": "", "trace": trace, "first_pass": wtext}
    text, provider, err, row = timed_step("3. Sarvam (not English)", "codemix", file_bytes, filename, "ml-mix", sarvam_key, groq_key, duration)
    trace.append(row)
    if not text:
        return {"text": "", "language": "unknown", "provider": provider, "ml_share": 0.0, "error": err, "trace": trace, "first_pass": ""}
    lang, share = classify_text(text)
    return {"text": text, "language": lang, "provider": provider, "ml_share": share, "error": "", "trace": trace, "first_pass": text}
