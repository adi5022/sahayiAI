"""One place that sends a message to a chat: simulated chats go to the local inbox, real ones to Telegram."""
from bia_hackaton_2026.pipeline import store


async def deliver(bot, chat_id, text: str) -> None:
    if store.is_virtual(chat_id):
        store.add_virtual(chat_id, "in", text)
    else:
        await bot.send_message(int(chat_id), text)
