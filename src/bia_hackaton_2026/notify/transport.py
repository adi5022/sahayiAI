"""One place that sends to a chat: simulated chats go to the local inbox, real ones to Telegram."""
from bia_hackaton_2026.pipeline import store


async def deliver(bot, chat_id, text: str) -> None:
    if store.is_virtual(chat_id):
        store.add_virtual(chat_id, "in", text)
    else:
        await bot.send_message(int(chat_id), text)


async def deliver_photo(bot, chat_id, photo: bytes, mime: str, caption: str = "") -> None:
    if store.is_virtual(chat_id):
        store.add_virtual(chat_id, "in", caption, media=store.save_virtual_media(photo, mime))
    else:
        await bot.send_photo(int(chat_id), photo=photo, caption=caption[:1000])
