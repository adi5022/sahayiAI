"""Export what is stored about members as JSON (medical history, incidents, chat log).

    uv run python -m bia_hackaton_2026.pipeline.export            # list members
    uv run python -m bia_hackaton_2026.pipeline.export <chat_id>  # write data/exports/<name>_<chat_id>.json

The files hold private health conversations: they stay in data/ (git-ignored). Do not share them.
"""
import json
import re
import sys

from bia_hackaton_2026.pipeline import memory, store


def write_export(chat_id) -> str:
    data = memory.export_member(chat_id)
    folder = store.DB_PATH.parent / "exports"
    folder.mkdir(parents=True, exist_ok=True)
    name = re.sub(r"[^A-Za-z0-9_-]+", "_", data["member"]["name"] or "member")
    path = folder / f"{name}_{chat_id}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    if len(sys.argv) < 2:
        for e in store._run("SELECT chat_id, name FROM elders ORDER BY created", many=True):
            print(e["chat_id"], e["name"])
    else:
        print(write_export(sys.argv[1]))
