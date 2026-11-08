"""Interactive simulator.  Run:  uv run python -m bia_hackaton_2026.sim

Type lines like   maya: I feel dizzy      ravi: /family ABC123      ravi: how is she today?
Media:            maya: @photo path/to/image.jpg some text      maya: @voice path/to/note.ogg
Quit with  :q   Show every inbox with  :inbox
"""
import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

from .telegram_sim import Sim


def _show(name: str, text: str) -> None:
    print(f"\n  [{name} receives]\n  " + text.replace("\n", "\n  "))


async def main() -> None:
    load_dotenv()
    sim = Sim()
    sim.on_message = _show
    print(__doc__)
    loop = asyncio.get_running_loop()
    while True:
        line = (await loop.run_in_executor(None, input, "\n> ")).strip()
        if line in (":q", "quit", "exit"):
            return
        if line == ":inbox":
            for n, c in sim.users.items():
                print(f"{n}: {len(sim.inbox[c])} message(s)")
            continue
        if ":" not in line:
            print("format:  name: message")
            continue
        name, _, text = line.partition(":")
        name, text = name.strip(), text.strip()
        if text.startswith("@photo ") or text.startswith("@voice "):
            kind, _, rest = text.partition(" ")
            path, _, caption = rest.partition(" ")
            kw = {"image": Path(path)} if kind == "@photo" else {"audio": Path(path)}
            await sim.send_media(name, caption, **kw)
        else:
            await sim.say(name, text)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, EOFError):
        sys.exit(0)
