"""Simulated family members that talk to the REAL bot's database.

Run the real Telegram bot as usual (your own account is the senior). Simulated family members are linked to
that senior in the shared database. Alerts the bot sends to them land in `virtual_inbox` (see
notify/transport.py); what they type is run through the real family handlers, and anything addressed to the
real senior (/tell) is delivered through Telegram.
"""
import os
from types import SimpleNamespace

from bia_hackaton_2026.pipeline import store
from bia_hackaton_2026.sim.telegram_sim import COMMANDS
from bia_hackaton_2026.telegram import bot as tg


class _LiveBot:
    """Real Telegram for real chats (the senior); no-ops for typing actions on simulated chats."""

    def __init__(self, real):
        self.real = real

    async def send_message(self, chat_id, text, **kw):
        if store.is_virtual(chat_id):
            store.add_virtual(chat_id, "in", text)
        elif self.real is None:
            raise RuntimeError("TELEGRAM_TOKEN is not set, cannot reach the real senior")
        else:
            await self.real.send_message(chat_id, text, **kw)

    async def send_photo(self, chat_id, photo=None, caption="", **kw):
        if self.real is None:
            raise RuntimeError("TELEGRAM_TOKEN is not set, cannot reach the real senior")
        await self.real.send_photo(chat_id, photo=photo, caption=caption, **kw)

    async def send_chat_action(self, *a, **kw):
        return None


def _update(chat_id: int, name: str, text: str):
    async def reply_text(t, **kw):
        store.add_virtual(chat_id, "in", t)

    msg = SimpleNamespace(text=text, reply_text=reply_text, caption=None)
    return SimpleNamespace(effective_chat=SimpleNamespace(id=chat_id), effective_user=SimpleNamespace(first_name=name),
                           effective_message=msg, message=msg)


_REAL = None


async def _real_bot():
    """One shared Telegram Bot for messages to real chats (e.g. /tell to your own account). None without a token."""
    global _REAL
    token = os.environ.get("TELEGRAM_TOKEN")
    if token and _REAL is None:
        from telegram import Bot
        _REAL = Bot(token)
        await _REAL.initialize()
    return _REAL


async def say(chat_id: int, name: str, text: str) -> None:
    """The simulated user `name` types `text` (a message or /command)."""
    store.add_virtual(chat_id, "out", text)
    try:
        bot = _LiveBot(await _real_bot())
        update = _update(chat_id, name, text)
        ctx = SimpleNamespace(bot=bot, args=[])
        if text.startswith("/"):
            cmd, _, rest = text[1:].partition(" ")
            handler = COMMANDS.get(cmd.split("@")[0].lower())
            if handler is None:
                store.add_virtual(chat_id, "in", f"(unknown command /{cmd})")
            else:
                ctx.args = rest.split()
                await handler(update, ctx)
        else:
            await tg.on_text(update, ctx)
    except Exception as e:
        store.add_virtual(chat_id, "in", f"(error: {e})")


async def say_photo(chat_id: int, name: str, photo: bytes, mime: str, caption: str = "") -> None:
    """The simulated user sends a photo (with an optional caption) to the bot, as a senior would."""
    store.add_virtual(chat_id, "out", caption, media=store.save_virtual_media(photo, mime))
    try:
        ctx = SimpleNamespace(bot=_LiveBot(await _real_bot()), args=[])
        await tg.respond(_update(chat_id, name, caption), ctx, text=caption, image=(photo, mime))
    except Exception as e:
        store.add_virtual(chat_id, "in", f"(error: {e})")
