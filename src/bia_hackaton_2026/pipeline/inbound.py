"""Transport-neutral inbound pipeline: voice/text/photo in -> Sahayi agent out.

Used by the Telegram bot (and can be used by Streamlit or any other transport).
Everything here is synchronous; async callers should run it in a thread.
"""
import threading
import time

from bia_hackaton_2026.agent import run_turn
from bia_hackaton_2026.message_processor.formatter import format_family_message
from bia_hackaton_2026.pipeline import memory, safety, store
from bia_hackaton_2026.stt.detect import detect_and_transcribe
from bia_hackaton_2026.pipeline.guard import OFF_TOPIC_REPLY, is_coding_request, looks_like_code
from bia_hackaton_2026.vlm import build_agent_input, describe_verified

_HISTORY: dict[str, list[dict]] = {}   # chat_id -> agent conversation history (in memory)
_LOCKS: dict[str, threading.Lock] = {}
_IMAGE_SEEN: set[str] = set()    # chats that already sent a photo
_IMAGE_ASKED: set[str] = set()   # chats we already asked for a photo (ask once, do not nag)

PHOTO_REQUEST = "If you can, please send me a photo of it. It will help your family and care team understand."
SCREENSHOT_REQUEST = "If you can, please send me a screenshot or photo of the message. It will help me check it."


def _lock(chat_id: str) -> threading.Lock:
    return _LOCKS.setdefault(chat_id, threading.Lock())


def _full_history(chat_id: str) -> list[dict]:
    """All recent entries of this chat with their time and topic; reloaded from SQLite after a restart."""
    if chat_id not in _HISTORY:
        _HISTORY[chat_id] = store.load_history_full(chat_id)
    return _HISTORY[chat_id]


def note_family_message(chat_id, who: str, text: str) -> None:
    """A family member's message was delivered to this member: add it to the member's working memory so that a reply
    such as "what did he say?" is understood as being about that message."""
    chat_id = str(chat_id)
    with _lock(chat_id):
        entry = memory.family_note_entry(who, text)
        _full_history(chat_id).append(entry)
        store.add_history(chat_id, [entry])


def reset_chat(chat_id) -> None:
    _HISTORY.pop(str(chat_id), None)
    store.clear_history(chat_id)  # a fresh conversation; the activity log family sees is kept
    _IMAGE_SEEN.discard(str(chat_id))
    _IMAGE_ASKED.discard(str(chat_id))


def handle_message(
    chat_id,
    text: str = "",
    audio: tuple[bytes, str] | None = None,    # (bytes, filename) e.g. a Telegram voice note
    image: tuple[bytes, str] | None = None,    # (bytes, mime) e.g. a Telegram photo
    llm_model: str | None = None,
) -> dict:
    """Process one inbound message. Returns dict:
    reply (text for the user), alert (family alert text), level, family_send, transcript, language,
    image_status, image_review, asked_image, input (what the LLM saw), seconds, error."""
    chat_id = str(chat_id)
    t0 = time.perf_counter()
    out = {"reply": "", "alert": "", "level": 1, "category": "general", "family_send": False, "transcript": "", "language": "",
           "image_status": "", "image_review": False, "asked_image": False, "input": "", "seconds": 0.0, "error": ""}

    with _lock(chat_id):  # one message at a time per chat keeps conversation history ordered
        parts = []
        if text.strip():
            parts.append(text.strip())

        if audio:
            stt = detect_and_transcribe(audio[0], audio[1], duration=(audio[2] if len(audio) > 2 else None))
            out["language"] = stt["language"]
            if stt["text"]:
                out["transcript"] = stt["text"]
                parts.append(stt["text"])
            elif not parts and not image:
                out["error"] = stt["error"] or "Could not transcribe the voice message."
                out["reply"] = "Sorry, I could not hear that clearly. Could you please say it again?"
                out["seconds"] = round(time.perf_counter() - t0, 2)
                return out

        image_desc = ""
        if image:
            v = describe_verified(image[0], image[1])
            image_desc = v["description"]
            out["image_status"] = v["status"]
            out["image_review"] = bool(v["needs_human_review"])
            if v["status"] != "verified" and not image_desc:
                out["error"] = v["error"]

        if is_coding_request("\n".join(parts)):  # not a wellbeing message: refuse without calling the model
            out["reply"] = OFF_TOPIC_REPLY
            out["seconds"] = round(time.perf_counter() - t0, 2)
            return out

        agent_input = build_agent_input("\n".join(parts), image_desc)
        if not agent_input:
            out["reply"] = "I did not receive anything I could understand. Please send a message, voice note or photo."
            out["seconds"] = round(time.perf_counter() - t0, 2)
            return out

        full = _full_history(chat_id)
        work = memory.working_set(full)   # working memory: current episode and topic only
        before = len(work)
        kw = {"model": llm_model} if llm_model else {}
        # the transport (Telegram) delivers messages itself; long-term notes go in as background, not as turns
        res = run_turn(agent_input, work, background=memory.background_block(chat_id), **kw)
        turn = res["turn"]
        # Safety floor: only ever raises. Looks at the member's recent words, not just this message.
        hit = safety.check([e["content"] for e in work[:before] if e["role"] == "user"][-3:] + [agent_input])
        if hit and safety.apply(res, hit):
            print(f"[SAHAYI] safety floor raised chat={chat_id} to level {hit.level} ({hit.reason})")
        if res["fallback"]:
            # The model could not answer (service problem). Never leave a member who reported an emergency with a bland reply:
            # repeat the safe instruction for their last situation and keep the family informed.
            recent = [m for m in store.recent_messages(chat_id, 8) if time.time() - m["ts"] < 3 * 3600]
            worst = max(recent, key=lambda m: m["level"], default=None)   # the worst recent situation, not just the last message
            if worst and worst["level"] >= 2:
                cat = worst.get("category") or "medical"
                turn.family.suggested_level, turn.family.send, turn.family.category = worst["level"], True, cat
                turn.family.summary = (f"Sahayi could not process the member's latest message because of a service problem. Earlier they were rated "
                                       f"level {worst['level']} ({cat}). Please check on them now.")[:400]
                res["user_message"] = safety.TEMPLATES.get(cat, safety.TEMPLATES["medical"])
                res["family_message"] = format_family_message(turn)
                res["family_sent"] = True
        new = [dict(e, ts=time.time(), cat=turn.family.category) for e in work[before:]]
        full.extend(new)
        store.add_history(chat_id, new)  # persist this exchange
        if not res["fallback"]:
            # long-term memory: the model's own note, or (so no incident goes unlogged) the factual family summary of any alert
            note = turn.note.strip() or (turn.family.summary if turn.family.suggested_level >= 2 else "")
            memory.remember(chat_id, turn.family.category, turn.family.suggested_level, note)
        out.update(
            reply=res["user_message"], alert=res["family_message"], level=turn.family.suggested_level,
            category=turn.family.category,
            family_send=res["family_sent"], input=agent_input,
        )
        if looks_like_code(out["reply"]):  # output guard: never send code to a member
            out["reply"] = OFF_TOPIC_REPLY
        # Visible injury and no photo yet: ask for one (once per chat). Added after the agent's own reply.
        if image:
            _IMAGE_SEEN.add(chat_id)
        elif turn.user.request_image and chat_id not in _IMAGE_SEEN and chat_id not in _IMAGE_ASKED:
            _IMAGE_ASKED.add(chat_id)
            out["reply"] = out["reply"] + "\n\n" + (SCREENSHOT_REQUEST if out["category"] == "scam" else PHOTO_REQUEST)
            out["asked_image"] = True
        if res["fallback"]:
            out["error"] = res["error"] or "Agent output was invalid; fallback reply used."

    out["seconds"] = round(time.perf_counter() - t0, 2)
    return out
