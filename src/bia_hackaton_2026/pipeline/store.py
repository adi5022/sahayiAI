"""SQLite storage for Sahayi: conversations, members (elders) with family join codes, and linked families.

File: <repo>/data/sahayi.db (git-ignored; override with SAHAYI_DB). Holds private conversation text:
treat it like personal data (see the retention note in the project README).
"""
import os
import secrets
import sqlite3
import time
from pathlib import Path

DB_PATH = Path(os.environ.get("SAHAYI_DB") or Path(__file__).resolve().parents[3] / "data" / "sahayi.db")
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # no 0/O/1/I to avoid mix-ups
CODE_LENGTH = 6

_SCHEMA = """
CREATE TABLE IF NOT EXISTS elders(chat_id TEXT PRIMARY KEY, name TEXT, code TEXT UNIQUE, created REAL);
CREATE TABLE IF NOT EXISTS family(chat_id TEXT, elder_chat_id TEXT, name TEXT, joined REAL,
                                  PRIMARY KEY(chat_id, elder_chat_id));
CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id TEXT, ts REAL, level INTEGER,
                                    said TEXT, reply TEXT, alert TEXT, photo INTEGER, review INTEGER);
CREATE TABLE IF NOT EXISTS history(id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id TEXT, ts REAL, role TEXT, content TEXT);
CREATE TABLE IF NOT EXISTS virtual_inbox(id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id TEXT, ts REAL, direction TEXT, text TEXT);
CREATE TABLE IF NOT EXISTS virtual_users(chat_id TEXT PRIMARY KEY, name TEXT, created REAL);
CREATE INDEX IF NOT EXISTS idx_virtual_chat ON virtual_inbox(chat_id, id);
CREATE INDEX IF NOT EXISTS idx_messages_chat ON messages(chat_id, ts);
CREATE INDEX IF NOT EXISTS idx_history_chat ON history(chat_id, id);
"""


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    c.executescript(_SCHEMA)
    return c


def _run(sql: str, args: tuple = (), many: bool = False):
    c = _conn()
    try:
        with c:
            cur = c.execute(sql, args)
            return [dict(r) for r in cur.fetchall()] if many else cur
    finally:
        c.close()


# ---------- members and codes ----------
def get_or_create_elder(chat_id, name: str) -> str:
    """Returns the family join code for this member, creating it on first use."""
    cid = str(chat_id)
    rows = _run("SELECT code FROM elders WHERE chat_id=?", (cid,), many=True)
    if rows:
        if name:
            _run("UPDATE elders SET name=? WHERE chat_id=?", (name, cid))
        return rows[0]["code"]
    while True:
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
        try:
            _run("INSERT INTO elders(chat_id,name,code,created) VALUES(?,?,?,?)", (cid, name, code, time.time()))
            return code
        except sqlite3.IntegrityError:
            continue  # code collision: draw another


def regenerate_code(chat_id) -> str:
    """New code for a member; existing linked families stay linked (use unlink_all_for_elder to cut them off)."""
    cid = str(chat_id)
    while True:
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
        try:
            _run("UPDATE elders SET code=? WHERE chat_id=?", (code, cid))
            return code
        except sqlite3.IntegrityError:
            continue


def elder_by_code(code: str) -> dict | None:
    rows = _run("SELECT chat_id,name FROM elders WHERE code=?", ((code or "").strip().upper(),), many=True)
    return rows[0] if rows else None


def is_elder(chat_id) -> bool:
    return bool(_run("SELECT 1 FROM elders WHERE chat_id=?", (str(chat_id),), many=True))


# ---------- families ----------
def link_family(family_chat, name: str, elder_chat) -> None:
    _run("INSERT OR REPLACE INTO family(chat_id,elder_chat_id,name,joined) VALUES(?,?,?,?)",
         (str(family_chat), str(elder_chat), name, time.time()))


def unlink_family(family_chat) -> int:
    return _run("DELETE FROM family WHERE chat_id=?", (str(family_chat),)).rowcount


def unlink_all_for_elder(elder_chat) -> int:
    return _run("DELETE FROM family WHERE elder_chat_id=?", (str(elder_chat),)).rowcount


def clear_all_families() -> int:
    """Log every family member out."""
    return _run("DELETE FROM family").rowcount


def families_of(elder_chat) -> dict[str, str]:
    return {r["chat_id"]: r["name"] for r in
            _run("SELECT chat_id,name FROM family WHERE elder_chat_id=?", (str(elder_chat),), many=True)}


def elders_of(family_chat) -> list[dict]:
    """Members this family chat is linked to: [{chat_id, name}]."""
    return _run("SELECT e.chat_id AS chat_id, e.name AS name FROM family f JOIN elders e ON e.chat_id=f.elder_chat_id "
                "WHERE f.chat_id=?", (str(family_chat),), many=True)


def is_family(chat_id) -> bool:
    return bool(_run("SELECT 1 FROM family WHERE chat_id=?", (str(chat_id),), many=True))


# ---------- activity log (what the family sees) ----------
def add_message(chat_id, level: int, said: str, reply: str, alert: str, photo: bool, review: bool) -> None:
    _run("INSERT INTO messages(chat_id,ts,level,said,reply,alert,photo,review) VALUES(?,?,?,?,?,?,?,?)",
         (str(chat_id), time.time(), level, said, reply, alert, int(photo), int(review)))


def recent_messages(chat_id, limit: int = 12) -> list[dict]:
    rows = _run("SELECT * FROM messages WHERE chat_id=? ORDER BY id DESC LIMIT ?", (str(chat_id), limit), many=True)
    return rows[::-1]


# ---------- agent conversation memory ----------
def add_history(chat_id, entries: list[dict]) -> None:
    for e in entries:
        _run("INSERT INTO history(chat_id,ts,role,content) VALUES(?,?,?,?)",
             (str(chat_id), time.time(), e["role"], e["content"]))


def load_history(chat_id, limit: int = 20) -> list[dict]:
    rows = _run("SELECT role,content FROM history WHERE chat_id=? ORDER BY id DESC LIMIT ?", (str(chat_id), limit), many=True)
    return rows[::-1]


def clear_history(chat_id) -> None:
    _run("DELETE FROM history WHERE chat_id=?", (str(chat_id),))


# ---------- virtual (simulated) chats ----------
# Simulated family members have no Telegram account. Their chat ids sit above any real Telegram id, and anything
# the bot sends to them is stored here instead of being sent. The simulator UI (sim/app.py) reads and writes this.
VIRTUAL_BASE = 10**13


def is_virtual(chat_id) -> bool:
    try:
        return int(chat_id) >= VIRTUAL_BASE
    except (TypeError, ValueError):
        return False


def add_virtual_user(name: str) -> int:
    """Create a simulated Telegram user and return its chat id."""
    rows = _run("SELECT MAX(CAST(chat_id AS INTEGER)) AS m FROM virtual_users", many=True)
    cid = max(VIRTUAL_BASE, rows[0]["m"] or 0) + 1
    _run("INSERT INTO virtual_users(chat_id,name,created) VALUES(?,?,?)", (str(cid), name.strip(), time.time()))
    return cid


def virtual_users() -> list[dict]:
    return _run("SELECT chat_id,name FROM virtual_users ORDER BY created", many=True)


def delete_virtual_user(chat_id) -> None:
    cid = str(chat_id)
    _run("DELETE FROM virtual_users WHERE chat_id=?", (cid,))
    _run("DELETE FROM virtual_inbox WHERE chat_id=?", (cid,))
    _run("DELETE FROM family WHERE chat_id=?", (cid,))
    _run("DELETE FROM elders WHERE chat_id=?", (cid,))


def add_virtual(chat_id, direction: str, text: str) -> None:
    """direction: 'in' = sent to the simulated user by the bot, 'out' = typed by the simulated user."""
    _run("INSERT INTO virtual_inbox(chat_id,ts,direction,text) VALUES(?,?,?,?)", (str(chat_id), time.time(), direction, text))


def virtual_messages(chat_id, after: int = 0, limit: int = 500) -> list[dict]:
    rows = _run("SELECT id,ts,direction,text FROM virtual_inbox WHERE chat_id=? AND id>? ORDER BY id DESC LIMIT ?",
                (str(chat_id), after, limit), many=True)
    return rows[::-1]
