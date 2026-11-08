"""One agent turn: retry once, fallback Turn, family-send rule. Delivery is left to the transport;
this returns everything so Telegram and the Streamlit UI can display or send it."""
import sys
import time

from bia_hackaton_2026.llm.response import MODEL, ask_llm
from bia_hackaton_2026.message_processor.formatter import format_family_message, format_user_message
from bia_hackaton_2026.message_processor.parser import parse_response
from bia_hackaton_2026.message_processor.schema import Turn
from bia_hackaton_2026.message_processor.validator import validate_turn

# The family alert contains an emoji; Windows consoles (cp1252) crash when printing it.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

MEMORY_LIMIT = 20                 # plain truncation of old messages


def run_turn(user_input: str, history: list[dict], model: str = MODEL, api_key: str | None = None,
             background: str = "") -> dict:
    """Appends the exchange to `history` (in place). Returns dict with turn, messages, send flag,
    raw LLM output, attempts, fallback flag, error, seconds, model."""
    t0 = time.perf_counter()
    raw, error, turn, attempts = "", "", None, 0
    for attempt in range(2):
        attempts = attempt + 1
        try:
            raw = ask_llm(user_input, history[-MEMORY_LIMIT:], model, api_key, background=background)
        except Exception as e:
            error = f"LLM call failed: {e}"
            continue
        turn = parse_response(raw)
        if turn is not None and validate_turn(turn):
            error = ""
            break
    else:
        turn = None

    fallback = turn is None
    if fallback:
        turn = Turn(
            user={"text": "I couldn't understand that. Please tell me again.", "follow_up": None},
            family={"summary": "Sahayi could not process the user's message.", "suggested_level": 1, "send": False},
        )

    user_message = format_user_message(turn)
    family_message = format_family_message(turn)
    family_sent = bool(turn.family.send or turn.family.suggested_level >= 2)
    history += [
        {"role": "user", "content": user_input},
        {"role": "assistant", "content": turn.model_dump_json()},
    ]
    return {
        "turn": turn, "user_message": user_message, "family_message": family_message,
        "family_sent": family_sent, "raw": raw, "attempts": attempts, "fallback": fallback,
        "error": error, "seconds": round(time.perf_counter() - t0, 2), "model": model,
    }
