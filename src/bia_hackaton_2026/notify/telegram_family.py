"""Family alerts over Telegram.

A family member joins a specific member by sending /family CODE to the bot. The code comes from the member
(/start or /code in their own chat). Links live in SQLite (pipeline/store.py); alerts only go to the families
linked to that member. There is no preset list: the code is the only way in.
"""
import os
import re
import time

from bia_hackaton_2026.notify.transport import deliver, deliver_photo
from bia_hackaton_2026.pipeline import store
from bia_hackaton_2026.pipeline.timeutil import fmt

DUPLICATE_WINDOW = 60                       # seconds: drop only an identical alert sent again within this window
_LAST_ALERT: dict[str, tuple[str, float]] = {}

CATEGORY_TEXT = {  # category -> (label, what the family should do)
    "medical": ("Medical", "Please check on them now. If it is an emergency, call 112 yourself."),
    "scam": ("Scam or fraud", "Please call them now. If money was sent or details were shared, they should call their bank "
                              "immediately and the cybercrime helpline 1930."),
    "safety": ("Safety", "Please check on them now. If they are in danger, call 112 yourself."),
    "emotional": ("Emotional wellbeing", "Please call or visit them soon. If they may harm themselves, call 112."),
    "general": ("General", "Please check in with them when you can."),
}


async def send_family_alert(bot, elder_chat_id, elder_name: str, alert_text: str, level: int,
                            photo: tuple[bytes, str] | None = None, category: str = "general") -> dict:
    """Send the alert to every family chat linked to this member. Returns dict:
    delivered (list of chat ids), failed (chat id -> reason), skipped (reason or ''), registered (int).
    `photo` = (bytes, mime) of the picture the member sent: it is forwarded after the text, because an automatic
    description is not enough for family to judge an injury."""
    out = {"delivered": [], "failed": {}, "skipped": "", "registered": 0}
    # FAMILY_INCLUDE_SELF=1 (demo with one Telegram account): the member's own chat may also receive the alert
    include_self = os.environ.get("FAMILY_INCLUDE_SELF") == "1"
    targets = {c: n for c, n in store.families_of(elder_chat_id).items() if include_self or c != str(elder_chat_id)}
    out["registered"] = len(targets)
    if not targets:
        return out

    # Every alert is sent: a new symptom must reach the family even if an alert went out minutes ago.
    # Only an identical alert repeated within a minute (double-processing) is dropped.
    last = _LAST_ALERT.get(str(elder_chat_id))
    if last and last[0] == alert_text and time.time() - last[1] < DUPLICATE_WINDOW:
        out["skipped"] = "identical alert already sent a moment ago"
        out["delivered"] = ["(identical alert)"]
        return out

    # the formatter's own header and type line repeat what the message header below already says
    body = re.sub(r"^.*(?:FAMILY ALERT|\*Type:\*).*\n?", "", alert_text, flags=re.M).replace("*", "").strip()
    label, advice = CATEGORY_TEXT.get(category, CATEGORY_TEXT["general"])
    icon = {3: "🚨 URGENT", 2: "⚠️ CHECK IN"}.get(level, "ℹ️ UPDATE")
    text = f"{icon} - SAHAYI\nType: {label}\n{fmt()}\n\nMember: {elder_name}\n\n{body}\n\n{advice}"
    for cid in targets:
        try:
            await deliver(bot, cid, text)
            out["delivered"].append(cid)
            if photo:
                await deliver_photo(bot, cid, photo[0], photo[1],
                                    f"Photo sent by {elder_name}, {fmt()}. The description above is automatic and may be "
                                    "wrong: please look at the photo yourself.")
        except Exception as e:  # e.g. the family member blocked the bot
            out["failed"][cid] = str(e)[:150]
    if out["delivered"]:
        _LAST_ALERT[str(elder_chat_id)] = (alert_text, time.time())
    return out
