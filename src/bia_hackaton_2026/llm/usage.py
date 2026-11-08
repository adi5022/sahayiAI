"""Usage tracking for every outside service Sahayi calls (Groq, NVIDIA, Gemini, Sarvam).

Each call is logged to SQLite (table usage_log, shared by the Telegram bot and the simulator server), so the usage panel in
the simulator shows both. Logging never raises: a failure here must not break a message.

Sarvam has no API for the credit balance (it is only shown in its dashboard under Settings > Billing), so the panel
estimates it: the member types the balance they see once, and the estimate subtracts the audio transcribed since then at
Sarvam's published price. Groq reports its rate-limit state in response headers, which are stored with each call.
"""
import time
from datetime import datetime



def _store():
    """Imported on use: pipeline imports the LLM modules, which import this one (a cycle at import time)."""
    from bia_hackaton_2026.pipeline import store
    return store

SARVAM_PRICE_PER_MIN = 0.49      # INR per minute of audio, pay-as-you-go (docs.sarvam.ai pricing); billed per second
SARVAM_FREE_CREDITS = 100.0      # INR given to a new verified account
GROQ_DAILY_TOKENS = 200_000      # per model, from Groq's rate-limit docs (not sent in headers)

_H = {"rl_req_rem": "x-ratelimit-remaining-requests", "rl_req_lim": "x-ratelimit-limit-requests",
      "rl_tok_rem": "x-ratelimit-remaining-tokens", "rl_tok_lim": "x-ratelimit-limit-tokens"}


def _num(v):
    try:
        return float(str(v).rstrip("sm"))
    except (TypeError, ValueError):
        return None


def record(provider: str, model: str, kind: str = "chat", ok: bool = True, status: int = 200, tokens_in: int = 0,
           tokens_out: int = 0, seconds: float = 0.0, audio_seconds: float = 0.0, headers=None) -> None:
    try:
        rl = {k: _num(headers.get(h)) if headers is not None else None for k, h in _H.items()}
        _store()._run(
            "INSERT INTO usage_log(ts,provider,model,kind,ok,status,tokens_in,tokens_out,seconds,audio_seconds,"
            "rl_req_rem,rl_req_lim,rl_tok_rem,rl_tok_lim) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (time.time(), provider, model, kind, int(ok), int(status or 0), int(tokens_in or 0), int(tokens_out or 0),
             round(seconds or 0.0, 3), round(audio_seconds or 0.0, 2), rl["rl_req_rem"], rl["rl_req_lim"],
             rl["rl_tok_rem"], rl["rl_tok_lim"]))
    except Exception:
        pass


def note_limit(label: str, message: str) -> None:
    """Groq's 429 text says exactly how much of a limit is used ("tokens per day (TPD): Limit 200000, Used 199127, Requested 3693").
    That is the real figure (it includes usage Sahayi did not make), so it is stored and shown next to our own count."""
    import json
    import re
    try:
        m = re.search(r"\((\w+)\): Limit (\d+), Used (\d+)", message or "")
        if m:
            _store().kv_set(f"limit:{label}", json.dumps({"abbr": m.group(1), "limit": int(m.group(2)), "used": int(m.group(3)), "ts": time.time()}))
    except Exception:
        pass


def set_sarvam_balance(credits_left: float) -> None:
    _store().kv_set("sarvam_credits_left", str(credits_left))
    _store().kv_set("sarvam_credits_since", str(time.time()))


def _midnight() -> float:
    return datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def summary() -> dict:
    now = time.time()
    day = _midnight()
    rows = _store()._run(
        "SELECT provider,model,kind,COUNT(*) calls,SUM(ok) ok,SUM(tokens_in+tokens_out) tokens,SUM(audio_seconds) audio,"
        "AVG(seconds) avg_s FROM usage_log WHERE ts>=? GROUP BY provider,model,kind ORDER BY provider,model", (day,), many=True)
    last = {}
    for r in _store()._run("SELECT provider,model,ts,ok,status,rl_req_rem,rl_req_lim,rl_tok_rem,rl_tok_lim FROM usage_log "
                        "ORDER BY id DESC LIMIT 400", many=True):
        key = (r["provider"], r["model"])
        if key not in last:
            last[key] = {"ts": r["ts"], "ok": bool(r["ok"]), "status": r["status"]}
        if "rl" not in last[key] and r["rl_tok_lim"]:
            last[key]["rl"] = {"ts": r["ts"], "req_rem": r["rl_req_rem"], "req_lim": r["rl_req_lim"],
                               "tok_rem": r["rl_tok_rem"], "tok_lim": r["rl_tok_lim"]}
    models = [{**r, "ok": r["ok"] or 0, "tokens": r["tokens"] or 0, "audio": r["audio"] or 0.0,
               "avg_s": round(r["avg_s"] or 0, 2), "last": last.get((r["provider"], r["model"]))} for r in rows]
    series = _store()._run(
        "SELECT CAST((ts-?)/60 AS INTEGER) m, provider, COUNT(*) calls, SUM(tokens_in+tokens_out) tokens "
        "FROM usage_log WHERE ts>=? GROUP BY m, provider", (now - 3600, now - 3600), many=True)
    # Sarvam credit estimate
    left, since = _store().kv_get("sarvam_credits_left"), _store().kv_get("sarvam_credits_since")
    audio_all = _store()._run("SELECT COALESCE(SUM(audio_seconds),0) s FROM usage_log WHERE provider='sarvam'", many=True)[0]["s"]
    sarvam = {"price_per_min": SARVAM_PRICE_PER_MIN, "free_credits": SARVAM_FREE_CREDITS, "audio_seconds_total": audio_all,
              "baseline": None, "estimated_left": None, "spent_since": None}
    if left is not None and since is not None:
        spent = _store()._run("SELECT COALESCE(SUM(audio_seconds),0) s FROM usage_log WHERE provider='sarvam' AND ts>=?",
                           (float(since),), many=True)[0]["s"] / 60 * SARVAM_PRICE_PER_MIN
        sarvam.update(baseline=float(left), since=float(since), spent_since=round(spent, 2),
                      estimated_left=round(max(float(left) - spent, 0.0), 2))
    t = _store()._run("SELECT MIN(ts) first, COUNT(*) n FROM usage_log", many=True)[0]
    import json
    import os
    limits = {}
    for r in _store()._run("SELECT key, value FROM kv WHERE key LIKE 'limit:%'", many=True):
        try:
            limits[r["key"][6:]] = json.loads(r["value"])
        except ValueError:
            pass
    config = {"groq_keys": 1 + sum(1 for i in (2, 3) if (os.environ.get(f"GROQ_API_KEY_{i}") or "").strip()),
              "gemini": bool((os.environ.get("GEMINI_API_KEY") or "").strip()),
              "nvidia": bool((os.environ.get("NVIDIA_API_KEY") or "").strip() and os.environ.get("NVIDIA_FALLBACK") == "1")}
    return {"limits": limits, "config": config, "now": now, "tracking": {"since": t["first"], "total_calls": t["n"]}, "models": models, "series": series, "sarvam": sarvam, "groq_daily_tokens": GROQ_DAILY_TOKENS}
