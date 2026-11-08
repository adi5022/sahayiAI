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

NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"

# Models checked against this project's accounts. Order = preference. Every one was run on the same urgency scenarios as the
# primary model (see README, "Fallback models").
_AGENT = (
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
    "qwen/qwen3.8-27b",
    "nvidia:meta/muse-glimmer-30b",   # last resort, slow (~15 s): 7/7 on the urgency scenarios; NVIDIA's gpt-oss-20b hangs
)
_SMALL = (
    "openai/gpt-oss-20b",
    "qwen/qwen3.8-27b",
    "openai/gpt-oss-120b",
    "nvidia:meta/muse-glimmer-30b",
)


def _available(chain: tuple[str, ...]) -> tuple[str, ...]:
    keep = []
    for m in chain:
        if m.startswith("nvidia:") and not os.environ.get("NVIDIA_API_KEY"):
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


def _split(entry: str) -> tuple[str, str]:
    return ("nvidia", entry[len("nvidia:"):]) if entry.startswith("nvidia:") else ("groq", entry)


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
    r = Groq(api_key=key, timeout=timeout, max_retries=0).chat.completions.create(
        model=model, messages=messages, temperature=temperature, reasoning_effort="low", **kw)
    return r.choices[0].message.content or ""


def _nvidia_call(model, messages, json_mode, temperature, max_tokens, timeout):
    client = openai.OpenAI(base_url=NVIDIA_BASE_URL, api_key=os.environ["NVIDIA_API_KEY"].strip(), timeout=timeout, max_retries=0)
    kw = {"max_tokens": max_tokens or 1500}
    if "gpt-oss" in model:
        kw["extra_body"] = {"reasoning_effort": "low"}
    if json_mode:
        try:
            r = client.chat.completions.create(model=model, messages=messages, temperature=temperature,
                                               response_format={"type": "json_object"}, **kw)
            return r.choices[0].message.content or ""
        except openai.BadRequestError:
            pass  # this model does not do JSON mode: ask without it (the caller validates the JSON anyway)
    r = client.chat.completions.create(model=model, messages=messages, temperature=temperature, **kw)
    return r.choices[0].message.content or ""


_RETRYABLE = (groq.APIConnectionError, groq.APITimeoutError, groq.InternalServerError,
              openai.APIConnectionError, openai.APITimeoutError, openai.InternalServerError)
_RATE = (groq.RateLimitError, openai.RateLimitError)
_GONE = (groq.NotFoundError, groq.PermissionDeniedError, openai.NotFoundError, openai.PermissionDeniedError,
         openai.AuthenticationError, groq.AuthenticationError)
_REJECTED = (groq.BadRequestError, openai.BadRequestError)


def chat(messages: list[dict], models: tuple[str, ...] = AGENT_CHAIN, api_key: str | None = None, json_mode: bool = False,
         temperature: float = 0.2, max_tokens: int | None = None, timeout: float = 30.0) -> str:
    """Returns the reply text from the first model that works."""
    global LAST_MODEL_USED
    models = _available(models)
    now = time.time()
    order = [m for m in models if _BLOCKED_UNTIL.get(m, 0) <= now]
    order += sorted((m for m in models if m not in order), key=lambda m: _BLOCKED_UNTIL[m])  # all limited: soonest first
    last: Exception | None = None
    for entry in order:
        provider, model = _split(entry)
        try:
            if provider == "nvidia":
                text = _nvidia_call(model, messages, json_mode, temperature, max_tokens, max(timeout, 25.0))  # slow model, last resort
            else:
                text = _groq_call(model, messages, api_key, json_mode, temperature, max_tokens, timeout)
        except _RATE as e:
            _BLOCKED_UNTIL[entry] = time.time() + _retry_after(e)
            print(f"[SAHAYI] {entry} rate limited, trying the next model")
            last = e
        except _RETRYABLE as e:
            _BLOCKED_UNTIL[entry] = time.time() + 10
            print(f"[SAHAYI] {entry} unreachable ({type(e).__name__}), trying the next model")
            last = e
        except _GONE as e:                       # model removed, or the key is not allowed to use it
            _BLOCKED_UNTIL[entry] = time.time() + 3600
            print(f"[SAHAYI] {entry} not available ({type(e).__name__}), trying the next model")
            last = e
        except _REJECTED as e:                   # e.g. the model produced invalid JSON this time
            last = e
        except (openai.APIStatusError, groq.APIStatusError) as e:   # e.g. 410 Gone for a retired model
            _BLOCKED_UNTIL[entry] = time.time() + 3600
            print(f"[SAHAYI] {entry} failed ({e.status_code}), trying the next model")
            last = e
        else:
            LAST_MODEL_USED = entry
            return text
    raise last or RuntimeError("no model available")
