"""Two separate memories for the care agent.

1. WORKING MEMORY (short term): what the model sees as the conversation. Only the current episode and the current topic:
   - an episode ends after EPISODE_GAP_SECONDS of silence, and
   - turns from an earlier, different topic (for example a scam) are dropped once the member moves to another topic
     (for example a health symptom), so facts do not leak from one into the other.
   Messages family members send to the member are added here too, so "what did he say?" means that message.

2. LONG-TERM MEMORY (member history): short dated notes about health reports and scam incidents, kept per member in
   SQLite (table memory_notes) and exportable as JSON. The model sees only a compact background block, labelled as
   background, never as something the member just said.
"""
import json
import time

from bia_hackaton_2026.pipeline import store
from bia_hackaton_2026.pipeline.timeutil import fmt

EPISODE_GAP_SECONDS = 20 * 60     # silence longer than this starts a new episode
WORKING_LIMIT = 16                # at most this many entries (user/assistant messages) go to the model
BACKGROUND_NOTES = 8              # long-term notes shown to the model
NEUTRAL = ("general", "")         # topics that never separate episodes


def category_of(entry: dict) -> str:
    """Topic of a history entry: assistant turns carry it in their JSON; user turns take the next assistant's; notes are neutral."""
    if entry.get("cat") is not None:
        return entry["cat"]
    if entry.get("role") == "assistant":
        try:
            return json.loads(entry["content"]).get("family", {}).get("category", "general") or "general"
        except (ValueError, TypeError, AttributeError):
            return "general"
    return ""


def annotate(entries: list[dict]) -> list[dict]:
    """Give every entry a 'cat': assistant entries from their JSON, the user entry before one takes the same topic."""
    out = [dict(e) for e in entries]
    for i, e in enumerate(out):
        if e.get("cat") is None:
            e["cat"] = category_of(e)
    for i in range(len(out) - 1):
        if out[i]["role"] == "user" and out[i + 1]["role"] == "assistant" and not out[i].get("note"):
            out[i]["cat"] = out[i + 1]["cat"]
    return out


def working_set(entries: list[dict], now: float | None = None) -> list[dict]:
    """The entries the model should see now (clean {role, content} dicts, oldest first)."""
    now = now or time.time()
    es = annotate(entries)
    # 1. current episode: walk back from the newest entry until a long silence
    keep: list[dict] = []
    last_ts = now
    for e in reversed(es):
        ts = e.get("ts", now)
        if last_ts - ts > EPISODE_GAP_SECONDS:
            break
        keep.append(e)
        last_ts = ts
    keep.reverse()
    # 2. current topic: the newest non-general topic wins; drop older turns of a different topic
    topic = next((e["cat"] for e in reversed(keep) if e["cat"] not in NEUTRAL), "")
    if topic:
        start = 0
        for i in range(len(keep) - 1, -1, -1):
            if keep[i]["cat"] not in NEUTRAL and keep[i]["cat"] != topic:
                start = i + 1
                break
        keep = keep[start:]
    return [{"role": e["role"], "content": e["content"]} for e in keep[-WORKING_LIMIT:]]


def family_note_entry(who: str, text: str) -> dict:
    """A history entry telling the model that a family member's message was delivered to the member."""
    return {"role": "user", "cat": "", "note": True, "ts": time.time(),
            "content": f'[FAMILY MESSAGE] {who} (family) sent the member this message at {fmt()}: "{text}". '
                       "It was delivered by Sahayi. The member may now reply to it."}


# ---------- long-term memory ----------
def background_block(chat_id) -> str:
    """Compact text for the model: the member's recent dated notes. Empty if there are none."""
    notes = store.recent_notes(chat_id, BACKGROUND_NOTES)
    if not notes:
        return ""
    lines = [f"- {fmt(n['ts'])} [{n['category']}]: {n['note']}" for n in notes]
    return ("# MEMBER BACKGROUND (older notes from earlier conversations, newest last)\n"
            "This is background only. It is NOT something the member just said. Use it for context (for example a repeated "
            "symptom, or a scam they were already targeted by). The current message alone decides the urgency and the type. "
            "Never repeat these notes back as if they were new.\n" + "\n".join(lines))


def remember(chat_id, category: str, level: int, note: str) -> None:
    """Append a note to the member's history (skips an identical note from the last 10 minutes)."""
    note = (note or "").strip()
    if not note or category in NEUTRAL:
        return
    last = store.recent_notes(chat_id, 1)
    if last and last[-1]["note"] == note and time.time() - last[-1]["ts"] < 600:
        return
    store.add_note(chat_id, category, level, note[:200])


def export_member(chat_id) -> dict:
    """Everything stored about one member as a JSON-ready dict: profile, history notes and chat log."""
    cid = str(chat_id)
    elder = store._run("SELECT name, code FROM elders WHERE chat_id=?", (cid,), many=True)
    notes = store.recent_notes(cid, 10_000)
    return {
        "member": {"chat_id": cid, "name": (elder[0]["name"] if elder else ""), "exported": fmt()},
        "medical_history": [{"time": fmt(n["ts"]), "ts": n["ts"], "level": n["level"], "note": n["note"]}
                            for n in notes if n["category"] == "medical"],
        "other_incidents": [{"time": fmt(n["ts"]), "ts": n["ts"], "type": n["category"], "level": n["level"], "note": n["note"]}
                            for n in notes if n["category"] != "medical"],
        "chat_log": [{"time": fmt(m["ts"]), "ts": m["ts"], "type": m.get("category") or "general", "level": m["level"],
                      "member_said": m["said"], "sahayi_replied": m["reply"], "family_alert": m["alert"],
                      "photo": bool(m["photo"])} for m in store.recent_messages(cid, 10_000)],
    }
