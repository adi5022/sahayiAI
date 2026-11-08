"""Drives the real handlers in telegram/bot.py with fake Telegram objects.

Several named users (a member, family members) chat with the bot in one process, so the whole family flow
(join by code, alerts, /status, /ask, /tell) can be tried and tested without Telegram accounts. The pipeline,
guards, database and LLM calls are the real ones; only Telegram's transport is replaced. Uses its own database.
"""
import asyncio
from pathlib import Path
from types import SimpleNamespace

from dotenv import load_dotenv

from bia_hackaton_2026.pipeline import store
from bia_hackaton_2026.telegram import bot as tg

SIM_DB = Path(__file__).resolve().parents[3] / "data" / "sahayi_sim.db"

COMMANDS = {
    "start": tg.start, "help": tg.help_cmd, "code": tg.my_code, "newcode": tg.new_code,
    "family": tg.family_join, "stopfamily": tg.family_leave, "status": tg.family_status, "history": tg.family_history,
    "ask": tg.family_ask, "tell": tg.family_tell,
}


class _Bot:
    """Stands in for telegram.Bot: records what the bot sends to each chat."""

    def __init__(self, sim: "Sim"):
        self.sim = sim

    async def send_message(self, chat_id, text, **kw):
        self.sim._deliver(int(chat_id), text)

    async def send_photo(self, chat_id, photo=None, caption="", **kw):
        self.sim._deliver(int(chat_id), f"[photo, {len(photo or b'')} bytes] {caption}")

    async def send_chat_action(self, *a, **kw):
        return None


class Sim:
    def __init__(self, fresh: bool = True):
        load_dotenv()
        store.DB_PATH = SIM_DB
        if fresh and SIM_DB.exists():
            SIM_DB.unlink()
        tg._JOIN_FAILS.clear()
        self.users: dict[str, int] = {}          # name -> chat id
        self.inbox: dict[int, list[str]] = {}     # chat id -> messages received from the bot
        self.bot = _Bot(self)
        self.on_message = None                    # optional callback(name, text), used by the REPL to print live

    # -- plumbing ---------------------------------------------------------------------------------
    def user(self, name: str) -> int:
        if name not in self.users:
            self.users[name] = 9000 + len(self.users) + 1
            self.inbox[self.users[name]] = []
        return self.users[name]

    def _deliver(self, chat_id: int, text: str) -> None:
        self.inbox.setdefault(chat_id, []).append(text)
        if self.on_message:
            name = next((n for n, c in self.users.items() if c == chat_id), str(chat_id))
            self.on_message(name, text)

    def _update(self, name: str, text: str):
        cid = self.user(name)

        async def reply_text(t, **kw):
            self._deliver(cid, t)

        msg = SimpleNamespace(text=text, reply_text=reply_text, caption=None)
        return SimpleNamespace(
            effective_chat=SimpleNamespace(id=cid), effective_user=SimpleNamespace(first_name=name),
            effective_message=msg, message=msg)

    # -- sending ----------------------------------------------------------------------------------
    async def say(self, name: str, text: str) -> list[str]:
        """`name` sends a text message or /command. Returns what that user's chat received in response."""
        cid = self.user(name)
        before = len(self.inbox[cid])
        update = self._update(name, text)
        if text.startswith("/"):
            cmd, _, rest = text[1:].partition(" ")
            handler = COMMANDS.get(cmd.split("@")[0].lower())
            if handler is None:
                self._deliver(cid, f"(unknown command /{cmd})")
            else:
                await handler(update, SimpleNamespace(bot=self.bot, args=rest.split()))
        else:
            await tg.on_text(update, SimpleNamespace(bot=self.bot, args=[]))
        return self.inbox[cid][before:]

    async def send_media(self, name: str, text: str = "", audio: Path | None = None, image: Path | None = None) -> list[str]:
        """`name` sends a voice note and/or photo (read from disk) to the member flow."""
        cid = self.user(name)
        before = len(self.inbox[cid])
        a = (Path(audio).read_bytes(), Path(audio).name) if audio else None
        i = (Path(image).read_bytes(), "image/jpeg") if image else None
        await tg.respond(self._update(name, text), SimpleNamespace(bot=self.bot, args=[]), text=text, audio=a, image=i)
        return self.inbox[cid][before:]

    def say_sync(self, name: str, text: str) -> list[str]:
        return asyncio.run(self.say(name, text))
