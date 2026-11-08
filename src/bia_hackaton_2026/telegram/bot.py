import asyncio
import logging
import os
import re
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

import time

from bia_hackaton_2026.llm.family_qa import answer_family_question, route_family_message
from bia_hackaton_2026.notify.telegram_family import send_family_alert
from bia_hackaton_2026.notify.transport import deliver
from bia_hackaton_2026.pipeline import handle_message, reset_chat, store
from bia_hackaton_2026.pipeline.inbound import note_family_message
from bia_hackaton_2026.pipeline.status import activity_log, is_elder, record, status_text
from bia_hackaton_2026.pipeline.timeutil import fmt
from telegram.error import NetworkError, TimedOut
from bia_hackaton_2026.stt.detect import detect_and_transcribe

# Setup clean console printing format
logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# httpx logs full request URLs, which contain the bot token. Keep them out of console/log files.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

WELCOME = (
    "Hello, I am Sahayi, your assistant. You can talk to me in English or Malayalam.\n\n"
    "Send me a voice message, a text message or a photo, and tell me how you are or what you need. "
    "I will listen and answer. Send /help to see everything I can do."
)
ALERT_FAILED = (
    "I could not reach your family contact just now. Please call them yourself. "
    "If you need urgent help, call 112 or ask someone nearby."
)
FRIENDLY_ERROR = "Sorry, something went wrong on my side. Please try again in a moment."


async def save_file(media, subdir: str, name: str) -> None:
    folder = Path(__file__).parent / subdir
    folder.mkdir(exist_ok=True)
    await (await media.get_file()).download_to_drive(folder / name)
    print(f"Saved to: {folder / name}")

# A catch-all function that prints any interaction to the console
async def log_interaction(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.effective_message

    print("\n--- NEW INTERACTION DETECTED ---")
    print(f"User: {user.first_name} {user.last_name or ''} (@{user.username or 'No Username'})")
    print(f"User ID: {user.id}")

    if message:
        if message.text:
            print(f"Interaction Type: Text / Command")
            print(f"Content: \"{message.text}\"")
        elif message.photo:
            print(f"Interaction Type: Photo")
            print(f"Caption: \"{message.caption or '[No Caption]'}\"")
            # Opt-in: SAVE_IMAGES=1 in .env; photo[-1] is the largest size
            if os.getenv("SAVE_IMAGES") == "1":
                await save_file(message.photo[-1], "images", "image.jpg")
        elif message.voice or message.audio:
            print(f"Interaction Type: Audio")
            # Opt-in: SAVE_AUDIO=1 in .env
            if os.getenv("SAVE_AUDIO") == "1":
                media = message.voice or message.audio
                await save_file(media, "audio", "audio.ogg")
        elif message.sticker:
            print(f"Interaction Type: Sticker")
            print(f"Emoji: {message.sticker.emoji}")
        else:
            print(f"Interaction Type: Other Media/File")
    print("---------------------------------\n")


async def download_bytes(media) -> bytes:
    """Download a Telegram file into memory (no file is written to disk)."""
    return bytes(await (await media.get_file()).download_as_bytearray())


async def respond(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str = "", audio=None, image=None) -> None:
    """Run the Sahayi pipeline (STT -> image check -> agent) off the event loop and reply in the chat."""
    chat_id = update.effective_chat.id
    await context.bot.send_chat_action(chat_id, ChatAction.TYPING)
    try:
        out = await asyncio.to_thread(handle_message, chat_id, text, audio, image)
    except Exception as e:  # never leave the user without an answer
        logging.exception("pipeline failed")
        await update.effective_message.reply_text(FRIENDLY_ERROR)
        return

    await update.effective_message.reply_text(out["reply"] or FRIENDLY_ERROR)
    record(chat_id, update.effective_user.first_name or "A Sahayi user", out)  # so family can ask about them

    # Console summary for the team (family alert delivery comes next, so it is only logged for now)
    print(f"[SAHAYI] chat={chat_id} level={out['level']} family_send={out['family_send']} "
          f"lang={out['language'] or '-'} image={out['image_status'] or '-'} {out['seconds']}s"
          + (f" ERROR={out['error']}" if out["error"] else ""))
    if out["transcript"]:
        print(f"[SAHAYI] transcript: {out['transcript']}")
    if out["family_send"] or out["level"] >= 2:
        name = update.effective_user.first_name or "A Sahayi user"
        res = await send_family_alert(context.bot, chat_id, name, out["alert"], out["level"], photo=image,
                                       category=out["category"])
        print(f"[SAHAYI] family alert: registered={res['registered']} delivered={res['delivered']} "
              f"failed={res['failed']} skipped={res['skipped'] or '-'}")
        if not res["delivered"]:
            # The agent's reply may have said the family is being informed; correct it if delivery failed.
            await update.effective_message.reply_text(ALERT_FAILED)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Activate the bot for this chat and start a fresh conversation."""
    if is_family_chat(update):   # a family member pressing /start again: reset nothing, point to what they can do
        await update.message.reply_text(
            "You are connected as family. Ask me about them in your own words, for example: How is she today?\n\n" + HELP_FAMILY)
        return
    reset_chat(update.effective_chat.id)
    code = store.get_or_create_elder(update.effective_chat.id, update.effective_user.first_name or "")
    await update.message.reply_text(WELCOME)
    await update.message.reply_text(
        f"Your family code is: {code}\n\n"
        "Share it with the family members you trust. They send this to me:\n"
        f"/family {code}\n"
        "and then they get alerts and can ask how you are. Send /newcode to disconnect everyone and get a new code."
    )


async def my_code(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show this member's family code and who is connected."""
    cid = update.effective_chat.id
    code = store.get_or_create_elder(cid, update.effective_user.first_name or "")
    fam = store.families_of(cid)
    names = ", ".join(fam.values()) if fam else "nobody yet"
    await update.message.reply_text(
        f"Your family code: {code}\nConnected family: {names}\n\n"
        "Family members send /family " + code + " to join. Send /newcode to disconnect everyone and get a new code."
    )


async def new_code(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Disconnect every family member of this member and issue a new code."""
    cid = update.effective_chat.id
    store.get_or_create_elder(cid, update.effective_user.first_name or "")
    removed = store.unlink_all_for_elder(cid)
    code = store.regenerate_code(cid)
    await update.message.reply_text(
        f"Done. {removed} family member(s) disconnected.\nYour new family code: {code}\n"
        f"Family members join again with: /family {code}"
    )


_JOIN_FAILS: dict[str, list[float]] = {}   # chat_id -> timestamps of wrong codes (brute-force guard)


async def family_join(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """A family member sends /family CODE to connect to a member and receive their alerts."""
    cid = str(update.effective_chat.id)
    if not context.args:
        await update.message.reply_text("To connect, ask the member for their family code and send:\n/family CODE")
        return
    recent = [t for t in _JOIN_FAILS.get(cid, []) if time.time() - t < 600]
    if len(recent) >= 5:
        await update.message.reply_text("Too many wrong codes. Please wait 10 minutes and try again.")
        return
    elder = store.elder_by_code(context.args[0])
    if not elder:
        _JOIN_FAILS[cid] = recent + [time.time()]
        await update.message.reply_text("That code is not valid. Please check it with the member and try again.")
        return
    store.link_family(cid, update.effective_user.first_name or "family", elder["chat_id"])
    await update.message.reply_text(
        f"You are now connected as family of {elder['name'] or 'the member'}. You will get a message here when "
        "Sahayi thinks they need attention.\n\n"
        "You can ask me about them any time:\n"
        "/status - latest summary and alerts\n"
        "or just type a question, for example: How is she today? What did she say last?\n\n"
        "Send /stopfamily to disconnect."
    )


async def family_leave(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    gone = store.unlink_family(update.effective_chat.id)
    await update.message.reply_text("You are disconnected and will no longer get family alerts." if gone else "You were not connected.")


def is_family_chat(update: Update) -> bool:
    """Connected family member who is not themselves a senior using the bot (one-account demos keep the senior flow)."""
    cid = update.effective_chat.id
    # Everyone presses /start first, which registers them as a member. A family member is whoever is linked to a member
    # and has nobody following them as a member themselves.
    return store.is_family(cid) and not store.is_followed(cid)


RELAY_INTENT = re.compile(r"^\s*(ask|tell|let|remind|message|send)\b", re.I)
THIRD_PERSON = re.compile(r"\b(how is|how's|is he|is she|he|she|him|her|his|elder|member|patient|status|condition|situation)\b", re.I)

NOT_FAMILY ="You are not connected to any member. Ask them for their family code and send /family CODE."


async def family_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    elders = store.elders_of(update.effective_chat.id)
    if not elders:
        await update.message.reply_text(NOT_FAMILY)
        return
    await update.message.reply_text(status_text(elders))


async def family_question(update: Update, context: ContextTypes.DEFAULT_TYPE, question: str) -> None:
    """Answer a family member's question about the senior from the activity log."""
    question = (question or "").strip()
    if not question:
        await update.message.reply_text("Ask me something about them, for example: How is she today?")
        return
    await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
    # Decide by meaning whether this is a message to pass on to the member or a question about them.
    route = await asyncio.to_thread(route_family_message, question)
    if route["intent"] == "relay" and route["message"]:
        await relay_to_member(update, context, route["message"], reworded=True)
        return
    elders = store.elders_of(update.effective_chat.id)
    try:
        answer = await asyncio.to_thread(answer_family_question, question, activity_log(elders))
    except Exception:
        logging.exception("family Q&A failed")
        answer = "Sorry, I could not look that up just now. Send /status for the latest summary."
    await update.message.reply_text(answer or "I do not have that information.")


async def relay_to_member(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, reworded: bool = False) -> None:
    """Deliver `text`, exactly as given, to the member(s) the family member is linked to, through the bot."""
    cid = update.effective_chat.id
    elders = store.elders_of(cid)
    if not elders:
        await update.message.reply_text(NOT_FAMILY)
        return
    msg = text.strip()
    if not msg:
        await update.message.reply_text("Write the message after the command, for example:\n/tell Please tell us where it hurts")
        return
    who = update.effective_user.first_name or "your family"
    done, failed = [], []
    for e in elders:
        try:
            await deliver(context.bot, e["chat_id"],
                          f"Message from {who} (your family), {fmt()}:\n{msg}\n\nYou can answer me here.")
            done.append(e["name"] or "the member")
            await asyncio.to_thread(note_family_message, e["chat_id"], who, msg)  # so the member's replies make sense
        except Exception as ex:
            failed.append(f"{e['name'] or 'the member'} ({str(ex)[:80]})")
    reply = (f"Sent to {', '.join(done)} at {fmt()}: \"{msg}\"" if done else "") + \
            (f"\nCould not send to: {', '.join(failed)}" if failed else "") + \
            ("\n(I put your request into a message. To send your exact words, use /tell.)" if reworded and done else "")
    await update.message.reply_text(reply.strip())
    print(f"[SAHAYI] relay from family {cid}: sent={done} failed={failed}")


async def family_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/history: dated health reports and incidents (scams, safety) the member has told Sahayi about."""
    elders = store.elders_of(update.effective_chat.id)
    if not elders:
        await update.message.reply_text(NOT_FAMILY)
        return
    blocks = []
    for e in elders:
        notes = store.recent_notes(e["chat_id"], 15)
        lines = [f"- {fmt(n['ts'])} [{n['category']}, level {n['level']}] {n['note']}" for n in notes]
        blocks.append(f"History of {e['name'] or 'the member'} (from what they told Sahayi, newest last):\n" +
                      ("\n".join(lines) if lines else "Nothing recorded yet."))
    await update.message.reply_text("\n\n".join(blocks)[:3900])


async def family_tell(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/tell MESSAGE: sends exactly these words (use it when the wording matters; otherwise just write naturally)."""
    await relay_to_member(update, context, " ".join(context.args))


async def family_ask(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/ask <question> - works even if the same account is also a senior (one-account demo)."""
    if not store.is_family(update.effective_chat.id):
        await update.message.reply_text(NOT_FAMILY)
        return
    await family_question(update, context, " ".join(context.args))


# Command menu: shown in Telegram's "/" menu and by /help
COMMANDS = [
    ("start", "Start Sahayi and get your family code"),
    ("help", "Show everything I can do"),
    ("code", "Show your family code and who is connected"),
    ("newcode", "Disconnect all family and get a new code"),
    ("family", "Family: connect to a member with /family CODE"),
    ("status", "Family: latest summary of the member"),
    ("history", "Family: health and incident history of the member"),
    ("ask", "Family: ask about the member"),
    ("tell", "Family: send your exact words to the member"),
    ("stopfamily", "Family: disconnect from the member"),
]

HELP_MEMBER = (
    "Sahayi - help for you\n\n"
    "Just talk to me: send a voice message, a text or a photo, in English or Malayalam.\n"
    "If you are hurt or unwell I will tell you what to do and let your family know.\n\n"
    "Commands:\n"
    "/start - begin a fresh conversation and get your family code\n"
    "/code - see your family code and who is connected\n"
    "/newcode - disconnect all family and get a new code\n"
    "/help - show this message\n\n"
    "In an emergency, call 112."
)
HELP_FAMILY = (
    "Sahayi - help for family\n\n"
    "You get an alert here when Sahayi thinks the member needs attention. You can also ask about them any time.\n\n"
    "Commands:\n"
    "/family CODE - connect to a member (ask them for their code)\n"
    "/status - latest summary and alerts\n"
    "/history - health reports and incidents (scams, safety) over time\n"
    "/ask QUESTION - or just type a question, for example: How is she today?\n"
    "/tell MESSAGE - send these exact words to the member (you can also just write, e.g. Ask her if she took her medicine)\n"
    "/stopfamily - disconnect from the member\n"
    "/help - show this message\n\n"
    "In an emergency, call 112."
)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Role-aware command list: family chats see the family commands, members the member commands."""
    cid = update.effective_chat.id
    if is_family_chat(update):
        text = HELP_FAMILY
    elif is_elder(cid):
        text = HELP_MEMBER
    else:  # not known yet: show both
        text = HELP_MEMBER + "\n\n" + "-" * 20 + "\n\n" + HELP_FAMILY
    await update.message.reply_text(text)


async def on_error(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Network hiccups reaching Telegram (ConnectTimeout, TimedOut) are logged in one line, not as a traceback."""
    err = context.error
    if isinstance(err, (TimedOut, NetworkError)):
        print(f"[SAHAYI] network problem talking to Telegram ({type(err).__name__}); the message may need to be re-sent")
        return
    logging.error("Unhandled error", exc_info=err)


async def set_menu(app) -> None:
    """Fill Telegram's built-in command menu (the / button next to the message box)."""
    from telegram import BotCommand
    await app.bot.set_my_commands([BotCommand(c, d) for c, d in COMMANDS])


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text
    cid = update.effective_chat.id
    # One-account demo: this chat is both the member and a family contact. Questions about a third person
    # ("how is he now", "the elder", "she") go to the family Q&A; everything else stays the member conversation.
    dual_family_question = store.is_family(cid) and store.is_followed(cid) and (RELAY_INTENT.match(text) or THIRD_PERSON.search(text))
    if is_family_chat(update) or dual_family_question:
        await family_question(update, context, text)
        return
    await respond(update, context, text=text)


async def on_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    media = update.message.voice or update.message.audio
    data = await download_bytes(media)
    if is_family_chat(update):  # a family member asking by voice
        stt = await asyncio.to_thread(detect_and_transcribe, data, "voice.ogg", None, None, getattr(media, "duration", None))
        await family_question(update, context, stt["text"])
        return
    if store.is_family(update.effective_chat.id) and store.is_followed(update.effective_chat.id):
        # One-account demo: decide by what was said, exactly like text ("tell him to relax" is a family request)
        stt = await asyncio.to_thread(detect_and_transcribe, data, "voice.ogg", None, None, getattr(media, "duration", None))
        said = stt["text"]
        if said and (RELAY_INTENT.match(said) or THIRD_PERSON.search(said)):
            await family_question(update, context, said)
            return
        if said:
            await respond(update, context, text=said)
            return
    # Telegram voice notes are OGG/Opus; our STT accepts them as is
    await respond(update, context, text=update.message.caption or "", audio=(data, "voice.ogg", getattr(media, "duration", None)))


async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Thank you, I am looking at your photo.")  # image check can take several seconds
    data = await download_bytes(update.message.photo[-1])  # largest size
    await respond(update, context, text=update.message.caption or "", image=(data, "image/jpeg"))


async def on_image_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    doc = update.message.document
    data = await download_bytes(doc)
    await respond(update, context, text=update.message.caption or "", image=(data, doc.mime_type or "image/jpeg"))


if __name__ == '__main__':
    load_dotenv()
    builder = ApplicationBuilder().post_init(set_menu).connect_timeout(20).read_timeout(30).write_timeout(30).pool_timeout(20)
    if os.environ.get("TELEGRAM_TEST") == "1":
        # Telegram TEST environment: separate accounts and bots, so several fake users can be created to try the
        # family flow. Uses its own token (from the test BotFather) and its own database file.
        builder = (builder.token(os.environ["TELEGRAM_TEST_TOKEN"])
                   .base_url("https://api.telegram.org/bot{token}/test")
                   .base_file_url("https://api.telegram.org/file/bot{token}/test"))
        store.DB_PATH = store.DB_PATH.with_name("sahayi_test.db")
        print(f"*** TELEGRAM TEST MODE *** (test server, database: {store.DB_PATH.name})")
    else:
        builder = builder.token(os.environ["TELEGRAM_TOKEN"])
    app = builder.build()
    app.add_error_handler(on_error)

    # 1. This prints ALL interactions to the console (Type filters.ALL catches text, photos, stickers, commands, etc.)
    app.add_handler(MessageHandler(filters.ALL, log_interaction), group=-1)

    # 2. These actually handle replying to the user
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("family", family_join))
    app.add_handler(CommandHandler("stopfamily", family_leave))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("code", my_code))
    app.add_handler(CommandHandler("newcode", new_code))
    app.add_handler(CommandHandler("status", family_status))
    app.add_handler(CommandHandler("history", family_history))
    app.add_handler(CommandHandler("ask", family_ask))
    app.add_handler(CommandHandler("tell", family_tell))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, on_voice))
    app.add_handler(MessageHandler(filters.PHOTO, on_photo))
    app.add_handler(MessageHandler(filters.Document.IMAGE, on_image_document))

    print("Bot is running... Go send something to it on Telegram!")
    app.run_polling(bootstrap_retries=-1)  # keep retrying if Telegram is unreachable at startup instead of crashing
