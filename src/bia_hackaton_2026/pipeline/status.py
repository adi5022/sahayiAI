"""Activity log of each senior (elder) the bot talks to, so family members can ask about them.
Stored in SQLite (pipeline/store.py), so it survives bot restarts."""
import time

from bia_hackaton_2026.pipeline import store
from bia_hackaton_2026.pipeline.timeutil import fmt

LEVEL_NAMES = {1: "calm", 2: "check in", 3: "EMERGENCY"}


def record(chat_id, name: str, out: dict) -> None:
    """Store one processed message (the dict returned by handle_message)."""
    store.get_or_create_elder(chat_id, name)
    store.add_message(
        chat_id, out["level"], (out["input"] or "")[:500], out["reply"][:300],
        out["alert"][:400] if (out["family_send"] or out["level"] >= 2) else "",
        bool(out["image_status"]), out["image_review"], out.get("category", "general"),
    )


def is_elder(chat_id) -> bool:
    return store.is_elder(chat_id)


def _ago(ts: float) -> str:
    """'12 min ago (7 Nov, 3:04 PM IST)': relative time plus the exact clock time."""
    s = int(time.time() - ts)
    rel = f"{s // 86400} d {(s % 86400) // 3600} h ago" if s >= 86400 else \
        f"{s // 3600} h {(s % 3600) // 60} min ago" if s >= 3600 else f"{s // 60} min ago" if s >= 60 else "just now"
    return f"{rel} ({fmt(ts)})"


def status_text(elders: list[dict]) -> str:
    """Deterministic summary for /status (no LLM). `elders` = [{chat_id, name}] the family is linked to."""
    if not elders:
        return "You are not linked to any member yet. Ask them for their family code and send /family CODE."
    blocks = []
    for e in elders:
        msgs = store.recent_messages(e["chat_id"], 12)
        if not msgs:
            blocks.append(f"Member: {e['name']}\nNo messages yet.")
            continue
        last = msgs[-1]
        lines = [f"Member: {e['name']}\nLast message: {_ago(last['ts'])}\n"
                 f"Current level: {last['level']} ({LEVEL_NAMES.get(last['level'], '?')}), type: {last.get('category') or 'general'}"]
        alerts = [x for x in msgs if x["alert"]]
        if alerts:
            a = alerts[-1]
            lines.append(f"Last alert ({_ago(a['ts'])}, level {a['level']}, {a.get('category') or 'general'}):\n{a['alert'].replace('*', '')}")
        else:
            lines.append("No alerts so far.")
        lines.append("Recent messages:\n" + "\n".join(
            f"- {_ago(x['ts'])}: {x['said'].replace(chr(10), ' ')[:140]}" for x in msgs[-4:]))
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def activity_log(elders: list[dict]) -> str:
    """Recent log of the linked members, used as context for a family member's free-form question."""
    parts = []
    for e in elders:
        rows = [f"[{_ago(x['ts'])}] level {x['level']} ({LEVEL_NAMES.get(x['level'], '?')}), type {x.get('category') or 'general'}; "
                f"member said/sent: {x['said'] or '(nothing)'}; Sahayi replied: {x['reply']}"
                + (f"; photo received (auto-described){' - needs human review' if x['review'] else ''}" if x["photo"] else "")
                + (f"; family alert: {x['alert']}" if x["alert"] else "")
                for x in store.recent_messages(e["chat_id"], 30)]
        parts.append(f"Member: {e['name']}\n" + ("\n".join(rows) if rows else "(no messages yet)"))
    return "\n\n".join(parts)
