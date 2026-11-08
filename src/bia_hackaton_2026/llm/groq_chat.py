"""Chat calls with automatic fallback across models and providers (Groq first, then others).

Free tiers have per-model rate limits (Groq: requests and tokens per minute and per day, shared by the whole organisation), so when
one model is rate limited the next one still has its own allowance. `chat()` walks a list of models, skips ones that recently
returned 429 (for as long as the provider's retry-after says), and moves on for timeouts, server errors, retired models and
requests a model rejects. Only if every model fails does it raise.

A chain entry is "model" (Groq) or "provider:model", for example "nvidia:openai/gpt-oss-20b". Providers other than Groq are used
only when their API key is set (NVIDIA_API_KEY), so the chain works with just the Groq key.
"""
import os
import time

import groq
import openai
from groq import Groq

from bia_hackaton_2026.llm import usage

NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"

# Models checked against this project's accounts. Order = preference. Every one was run on the same urgency scenarios as the
# primary model (see README, "Fallback models").
_AGENT = (
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
    "qwen/qwen3.8-27b",
    "gemini:gemini-3.5-flash",        # its own daily allowance; the Groq models share small per-model limits
    "nvidia:meta/muse-glimmer-30b",   # off unless NVIDIA_FALLBACK=1: it timed out after 30 s on every request in testing
)
_SMALL = (
    "openai/gpt-oss-20b",
    "qwen/qwen3.8-27b",
    "openai/gpt-oss-120b",
    "gemini:gemini-3.5-flash",
    "nvidia:meta/muse-glimmer-30b",
)


def _available(chain: tuple[str, ...]) -> tuple[str, ...]:
    keep = []
    for m in chain:
        # NVIDIA's free endpoint timed out after 30 s on every request in testing, which made rate-limited messages
        # slow for nothing, so it is only used when explicitly switched on (NVIDIA_FALLBACK=1) and a key is set
        if m.startswith("nvidia:") and not (os.environ.get("NVIDIA_API_KEY") and os.environ.get("NVIDIA_FALLBACK") == "1"):
            continue
        if m.startswith("gemini:") and not os.environ.get("GEMINI_API_KEY"):
            continue
        keep.append(m)
    return tuple(keep)


# Callers pass these; chat() drops entries whose provider has no key at call time (keys load from .env after import).
AGENT_CHAIN = _AGENT
SMALL_CHAIN = _SMALL

_BLOCKED_UNTIL: dict[str, float] = {}   # chain entry -> time.time() until which it is skipped
LAST_MODEL_USED = ""                    # for logging


def chain_from(first: str | None, base: tuple[str, ...]) -> tuple[str, ...]:
    """Preferred model first, then the rest of the chain."""
    base = _available(base)
    return (first, *[m for m in base if m != first]) if first else base


GROQ_KEYS = 3   # GROQ_API_KEY, GROQ_API_KEY_2, GROQ_API_KEY_3 (each from a separate account has its own limits)


def _key_name(idx: int) -> str:
    return "GROQ_API_KEY" if idx == 1 else f"GROQ_API_KEY_{idx}"


def _key_value(idx: int) -> str:
    return (os.environ.get(_key_name(idx)) or "").strip()


def expand(models: tuple[str, ...]) -> tuple[str, ...]:
    """Groq models are tried with the first key, then with the second and third key if those are set ("model#2", "model#3"), so a
    key whose daily allowance is used up hands over to the next one. Non-Groq entries (Gemini, ...) stay after all Groq entries."""
    groq_models = [m for m in models if _split(m)[0] == "groq"]
    others = [m for m in models if _split(m)[0] != "groq"]
    out = []
    for idx in range(1, GROQ_KEYS + 1):
        if idx == 1 or _key_value(idx):
            out += [m if idx == 1 else f"{m}#{idx}" for m in groq_models]
    return tuple(out + others)


def _parse(entry: str) -> tuple[str, str, int]:
    base, _, k = entry.partition("#")
    provider, model = _split(base)
    return provider, model, int(k or 1)


def _split(entry: str) -> tuple[str, str]:
    entry = entry.partition("#")[0]
    for p in ("nvidia", "gemini"):
        if entry.startswith(p + ":"):
            return p, entry[len(p) + 1:]
    return "groq", entry


def _retry_after(err, default: float = 30.0) -> float:
    try:
        return float(err.response.headers.get("retry-after", default))
    except (TypeError, ValueError, AttributeError):
        return default


def _groq_call(model, messages, api_key, json_mode, temperature, max_tokens, timeout):
    key = (api_key or os.environ.get("GROQ_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("Groq API key missing (set GROQ_API_KEY in .env or the sidebar).")
    kw = {"response_format": {"type": "json_object"}} if json_mode else {}
    if max_tokens:
        kw["max_completion_tokens"] = max_tokens
    raw = Groq(api_key=key, timeout=timeout, max_retries=0).chat.completions.with_raw_response.create(
        model=model, messages=messages, temperature=temperature, reasoning_effort="low", **kw)
    r = raw.parse()
    u = getattr(r, "usage", None)
    meta = {"tokens_in": getattr(u, "prompt_tokens", 0), "tokens_out": getattr(u, "completion_tokens", 0), "headers": raw.headers}
    return r.choices[0].message.content or "", meta


def _nv_meta(r) -> dict:
    u = getattr(r, "usage", None)
    return {"tokens_in": getattr(u, "prompt_tokens", 0), "tokens_out": getattr(u, "completion_tokens", 0), "headers": None}


class GeminiLimit(Exception):
    """Gemini answered 429 (quota). Carries the wait the service asked for, when it said."""
    def __init__(self, wait: float = 30.0):
        super().__init__("gemini rate limited")
        self.wait = wait


def _gemini_call(model, messages, json_mode, temperature, max_tokens, timeout):
    from google import genai
    from google.genai import errors, types
    key = (os.environ.get("GEMINI_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("Gemini API key missing")
    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    contents = [types.Content(role="model" if m["role"] == "assistant" else "user", parts=[types.Part(text=m["content"])])
                for m in messages if m["role"] != "system"]
    cfg = types.GenerateContentConfig(system_instruction=system or None, temperature=temperature, max_output_tokens=max_tokens or 2048,
                                      response_mime_type="application/json" if json_mode else None)
    try:
        client = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=int(timeout * 1000)))   # keep a reference: a
        r = client.models.generate_content(model=model, contents=contents, config=cfg)                  # temporary client is closed early
    except errors.APIError as e:
        if getattr(e, "code", 0) == 429:
            raise GeminiLimit() from e
        raise
    u = getattr(r, "usage_metadata", None)
    return (r.text or "").strip(), {"tokens_in": getattr(u, "prompt_token_count", 0) or 0, "tokens_out": getattr(u, "candidates_token_count", 0) or 0, "headers": None}


def _nvidia_call(model, messages, json_mode, temperature, max_tokens, timeout):
    client = openai.OpenAI(base_url=NVIDIA_BASE_URL, api_key=os.environ["NVIDIA_API_KEY"].strip(), timeout=timeout, max_retries=0)
    kw = {"max_tokens": max_tokens or 1500}
    if "gpt-oss" in model:
        kw["extra_body"] = {"reasoning_effort": "low"}
    if json_mode:
        try:
            r = client.chat.completions.create(model=model, messages=messages, temperature=temperature,
                                               response_format={"type": "json_object"}, **kw)
            return r.choices[0].message.content or "", _nv_meta(r)
        except openai.BadRequestError:
            pass  # this model does not do JSON mode: ask without it (the caller validates the JSON anyway)
    r = client.chat.completions.create(model=model, messages=messages, temperature=temperature, **kw)
    return r.choices[0].message.content or "", _nv_meta(r)


_RETRYABLE = (groq.APIConnectionError, groq.APITimeoutError, groq.InternalServerError,
              openai.APIConnectionError, openai.APITimeoutError, openai.InternalServerError)
_RATE = (groq.RateLimitError, openai.RateLimitError)
_GONE = (groq.NotFoundError, groq.PermissionDeniedError, openai.NotFoundError, openai.PermissionDeniedError,
         openai.AuthenticationError, groq.AuthenticationError)
_REJECTED = (groq.BadRequestError, openai.BadRequestError)


import threading

_TL = threading.local()   # which key the last with_groq_keys call in this thread ended on


def key_tag() -> str:
    """'' for key 1, '#2' / '#3' for the others: appended to model names in the usage log so each key shows separately."""
    idx = getattr(_TL, 'idx', 1)
    return '' if idx == 1 else f'#{idx}'


_AUDIO_BLOCKED: dict[tuple[str, int], float] = {}   # (kind, key number) -> time until which that key is skipped for that kind


def with_groq_keys(call, api_key: str | None = None, kind: str = "groq", timeout: float = 30.0, model: str = ""):
    """Run `call(client)` with the first Groq key and, if it is rate limited, with the next keys (GROQ_API_KEY_2, _3). Used for speech and
    vision, which call Groq directly instead of through chat(). Raises the last rate-limit error when every key is limited."""
    keys = [(1, (api_key or _key_value(1)))] + [(i, _key_value(i)) for i in range(2, GROQ_KEYS + 1)]
    keys = [(i, k) for i, k in keys if k]
    if not keys:
        raise RuntimeError("Groq API key missing (set GROQ_API_KEY in .env).")
    now = time.time()
    order = [x for x in keys if _AUDIO_BLOCKED.get((kind, x[0]), 0) <= now]
    order += sorted((x for x in keys if x not in order), key=lambda x: _AUDIO_BLOCKED[(kind, x[0])])   # all limited: soonest first
    last: Exception | None = None
    for idx, key in order:
        try:
            result = call(Groq(api_key=key, timeout=timeout, max_retries=0))
            _TL.idx = idx
            return result
        except groq.RateLimitError as e:
            label = model + ("" if idx == 1 else f"#{idx}")
            if model:
                usage.record("groq", label, "stt" if kind == "whisper" else "vision", False, 429, headers=getattr(getattr(e, "response", None), "headers", None))
                usage.note_limit(label, str(e))
            _AUDIO_BLOCKED[(kind, idx)] = time.time() + _retry_after(e)
            print(f"[SAHAYI] Groq key {idx} rate limited for {kind}, trying the next key")
            last = e
    raise last


MAX_WAIT = 10.0   # when every model is rate limited, wait up to this long for the soonest one to recover


def chat(messages: list[dict], models: tuple[str, ...] = AGENT_CHAIN, api_key: str | None = None, json_mode: bool = False,
         temperature: float = 0.2, max_tokens: int | None = None, timeout: float = 30.0) -> str:
    """Returns the reply text from the first model that works."""
    models = expand(_available(models))
    last: Exception | None = None
    for round_ in range(2):
        try:
            return _walk(messages, models, api_key, json_mode, temperature, max_tokens, timeout)
        except Exception as e:
            last = e
            blocked = [_BLOCKED_UNTIL.get(m, 0) - time.time() for m in models]
            wait = min(blocked) if blocked else 0
            # Rate limits recover in seconds: waiting a moment beats failing the member's message
            if round_ == 0 and isinstance(e, _RATE) and 0 < wait <= MAX_WAIT:
                print(f"[SAHAYI] every model is rate limited, waiting {wait:.1f}s")
                time.sleep(wait + 0.3)
                continue
            break
    raise last or RuntimeError("no model available")


def _walk(messages: list[dict], models: tuple[str, ...] = AGENT_CHAIN, api_key: str | None = None, json_mode: bool = False,
         temperature: float = 0.2, max_tokens: int | None = None, timeout: float = 30.0) -> str:
    """One pass over the chain: returns the reply text from the first model that works."""
    global LAST_MODEL_USED
    now = time.time()
    order = [m for m in models if _BLOCKED_UNTIL.get(m, 0) <= now]
    order += sorted((m for m in models if m not in order), key=lambda m: _BLOCKED_UNTIL[m])  # all limited: soonest first
    last: Exception | None = None
    for entry in order:
        provider, model, kidx = _parse(entry)
        label = model if kidx == 1 else f"{model}#{kidx}"   # shown in the Usage panel, one card per key
        t0 = time.perf_counter()
        try:
            if provider == "gemini":
                text, meta = _gemini_call(model, messages, json_mode, temperature, max_tokens, 25.0)
            elif provider == "nvidia":
                text, meta = _nvidia_call(model, messages, json_mode, temperature, max_tokens, max(timeout, 25.0))  # slow model, last resort
            else:
                text, meta = _groq_call(model, messages, (api_key if kidx == 1 and api_key else _key_value(kidx)), json_mode, temperature, max_tokens, timeout)
        except _RATE as e:
            usage.record(provider, label, "chat", False, 429, seconds=time.perf_counter() - t0,
                         headers=getattr(getattr(e, "response", None), "headers", None))
            usage.note_limit(label, str(e))
            _BLOCKED_UNTIL[entry] = time.time() + _retry_after(e)
            print(f"[SAHAYI] {entry} rate limited, trying the next model")
            last = e
        except GeminiLimit as e:
            usage.record(provider, label, "chat", False, 429, seconds=time.perf_counter() - t0)
            _BLOCKED_UNTIL[entry] = time.time() + e.wait
            print(f"[SAHAYI] {entry} rate limited, trying the next model")
            last = e
        except _RETRYABLE as e:
            usage.record(provider, label, "chat", False, 0, seconds=time.perf_counter() - t0)
            _BLOCKED_UNTIL[entry] = time.time() + 10
            print(f"[SAHAYI] {entry} unreachable ({type(e).__name__}), trying the next model")
            last = e
        except _GONE as e:                       # model removed, or the key is not allowed to use it
            _BLOCKED_UNTIL[entry] = time.time() + 3600
            print(f"[SAHAYI] {entry} not available ({type(e).__name__}), trying the next model")
            last = e
        except _REJECTED as e:                   # e.g. the model produced invalid JSON this time
            last = e
        except RuntimeError as e:
            if "key missing" in str(e):          # no key configured: do not try this provider again this session
                _BLOCKED_UNTIL[entry] = time.time() + 3600
            else:
                _BLOCKED_UNTIL[entry] = time.time() + 20
            last = e
        except Exception as e:                   # any other failure of one provider must not stop the others
            usage.record(provider, label, "chat", False, 0, seconds=time.perf_counter() - t0)
            _BLOCKED_UNTIL[entry] = time.time() + 20
            print(f"[SAHAYI] {entry} failed ({type(e).__name__}), trying the next model")
            last = e
        except (openai.APIStatusError, groq.APIStatusError) as e:   # e.g. 410 Gone for a retired model
            _BLOCKED_UNTIL[entry] = time.time() + 3600
            print(f"[SAHAYI] {entry} failed ({e.status_code}), trying the next model")
            last = e
        else:
            usage.record(provider, label, "chat", True, 200, seconds=time.perf_counter() - t0, **meta)
            LAST_MODEL_USED = entry
            return text
    raise last or RuntimeError("no model available")
