"""Backend for the Telegram-style simulator UI (sim-ui/).

Run:  uv run python -m bia_hackaton_2026.sim.server      (API on http://127.0.0.1:8765, also serves sim-ui/dist)

Works next to the real bot: start the real bot as usual (your own Telegram account stays a real user), and add
simulated users here. They share the bot's database, so a simulated family member can join your real account's code,
receive its alerts and /tell it; a simulated user can also be a senior and talk to the bot itself.
"""
import asyncio
import os
import time
from pathlib import Path

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from bia_hackaton_2026.llm import usage
from bia_hackaton_2026.pipeline import reset_chat, store
from bia_hackaton_2026.sim import live
from bia_hackaton_2026.stt.detect import classify_text, detect_and_transcribe
from bia_hackaton_2026.stt.transcribe import transcribe_audio
from bia_hackaton_2026.telegram.bot import COMMANDS

load_dotenv()
DIST = Path(__file__).resolve().parents[3] / "sim-ui" / "dist"
PORT = int(os.environ.get("SIM_PORT", "8765"))
TAKES_ARGS = {"family", "ask", "tell"}

app = FastAPI(title="Sahayi Telegram simulator")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                   allow_methods=["*"], allow_headers=["*"])
_PENDING: dict[int, int] = {}       # chat id -> messages still being processed
_TASKS: set[asyncio.Task] = set()


class NewUser(BaseModel):
    name: str


class Outgoing(BaseModel):
    text: str


@app.get("/api/commands")
def commands():
    return [{"command": c, "description": d, "args": c in TAKES_ARGS} for c, d in COMMANDS]


@app.get("/api/users")
def users():
    out = []
    for u in store.virtual_users():
        msgs = store.virtual_messages(u["chat_id"], limit=1)
        last = msgs[-1] if msgs else None
        out.append({"chat_id": int(u["chat_id"]), "name": u["name"], "last": last,
                    "typing": _PENDING.get(int(u["chat_id"]), 0) > 0})
    return out


@app.post("/api/users")
def add_user(body: NewUser):
    if not body.name.strip():
        raise HTTPException(400, "name required")
    return {"chat_id": store.add_virtual_user(body.name), "name": body.name.strip()}


@app.delete("/api/users/{chat_id}")
def delete_user(chat_id: int):
    store.delete_virtual_user(chat_id)
    reset_chat(chat_id)   # also clears what the running server keeps in memory for this chat
    return {"ok": True}


@app.get("/api/users/{chat_id}/messages")
def messages(chat_id: int, after: int = 0):
    return {"messages": store.virtual_messages(chat_id, after=after), "typing": _PENDING.get(chat_id, 0) > 0}


@app.post("/api/users/{chat_id}/send")
async def send(chat_id: int, body: Outgoing):
    name = next((u["name"] for u in store.virtual_users() if int(u["chat_id"]) == chat_id), None)
    if name is None:
        raise HTTPException(404, "unknown user")
    text = body.text.strip()
    if not text:
        raise HTTPException(400, "empty message")
    _PENDING[chat_id] = _PENDING.get(chat_id, 0) + 1

    async def run():
        try:
            await live.say(chat_id, name, text)
        finally:
            _PENDING[chat_id] -= 1

    task = asyncio.create_task(run())
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return {"ok": True}


@app.post("/api/users/{chat_id}/photo")
async def send_photo(chat_id: int, photo: UploadFile = File(...), caption: str = Form("")):
    name = next((u["name"] for u in store.virtual_users() if int(u["chat_id"]) == chat_id), None)
    if name is None:
        raise HTTPException(404, "unknown user")
    data = await photo.read()
    if not data or len(data) > 10 * 1024 * 1024:
        raise HTTPException(400, "photo missing or larger than 10 MB")
    mime = photo.content_type if (photo.content_type or "").startswith("image/") else "image/jpeg"
    _PENDING[chat_id] = _PENDING.get(chat_id, 0) + 1

    async def run():
        try:
            await live.say_photo(chat_id, name, data, mime, caption.strip())
        finally:
            _PENDING[chat_id] -= 1

    task = asyncio.create_task(run())
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return {"ok": True}


@app.post("/api/transcribe")
async def transcribe(audio: list[UploadFile] = File(...), durations: str = Form(""), language: str = Form("auto")):
    """Voice note -> text. The browser sends the recording in segments of at most 25 s (Sarvam rejects audio over 30 s).
    language: "en" = Whisper only (no Sarvam credits used), "ml" = Sarvam only, "auto" = detect (Sarvam first, then Whisper for English).
    Nothing is sent to a chat."""
    files = [(await f.read(), f.filename or "voice.webm") for f in audio]
    if not files or any(not d for d, _ in files) or sum(len(d) for d, _ in files) > 25 * 1024 * 1024:
        raise HTTPException(400, "audio missing or larger than 25 MB")
    durs = [float(x) if x.strip() else None for x in durations.split(",")] if durations else []

    def one(i: int):
        data, name = files[i]
        d = durs[i] if i < len(durs) else None
        if language == "en":
            return transcribe_audio(data, name, "en", None, None, d)
        if language == "ml":
            return transcribe_audio(data, name, "ml-mix", None, None, d)
        r = detect_and_transcribe(data, name, None, None, d)
        return r["text"], r["provider"], r["error"]

    t0 = time.perf_counter()
    results = await asyncio.gather(*[asyncio.to_thread(one, i) for i in range(len(files))])
    text = " ".join(t for t, _, _ in results if t).strip()
    err = next((e for _, _, e in results if e), "")
    providers = {p for _, p, _ in results}
    return {"text": text, "language": classify_text(text)[0] if text else "unknown",
            "provider": "sarvam" if "sarvam" in providers else "groq", "error": "" if text else err,
            "seconds": round(time.perf_counter() - t0, 2), "segments": len(files)}


class SarvamBalance(BaseModel):
    credits_left: float


@app.get("/api/usage")
def get_usage():
    return usage.summary()


@app.post("/api/usage/sarvam-credits")
def set_sarvam_credits(body: SarvamBalance):
    usage.set_sarvam_balance(body.credits_left)
    return usage.summary()["sarvam"]


@app.get("/api/media/{name}")
def media(name: str):
    f = store.media_dir() / Path(name).name      # Path().name blocks path traversal
    if not f.is_file():
        raise HTTPException(404)
    return FileResponse(f)


if DIST.exists():  # built UI (npm run build in sim-ui/)
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        f = DIST / path
        return FileResponse(f if path and f.is_file() else DIST / "index.html")


if __name__ == "__main__":
    print(f"Sahayi simulator API on http://127.0.0.1:{PORT}" + ("  (UI served at the same address)" if DIST.exists()
          else "  (UI not built: run it with npm run dev in sim-ui/)"))
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
