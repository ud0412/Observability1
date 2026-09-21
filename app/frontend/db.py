"""
frontend SQLite DB — 모델 설정 / 채팅 세션 / 메시지 저장.

파일: FRONTEND_DB 환경변수 (기본 /data/frontend.db)
"""
import logging
import os
import sqlite3
import time
import uuid

log = logging.getLogger("frontend")

DB_PATH = os.environ.get("FRONTEND_DB", "/data/frontend.db")

DEFAULT_SETTINGS = {
    "base_url": os.environ.get("DEFAULT_BASE_URL", "http://mock-llm:8100/v1"),
    "api_key": "mock-key",
    "model": "mock-gpt-4o",
    "temperature": 0.7,
    "max_tokens": None,  # None = 모델 기본값
    "system_prompt": "You are a helpful assistant.",
}


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    with get_conn() as c:
        c.execute(
            """CREATE TABLE IF NOT EXISTS settings(
                id INTEGER PRIMARY KEY CHECK (id = 1),
                base_url TEXT NOT NULL,
                api_key TEXT NOT NULL DEFAULT '',
                model TEXT NOT NULL,
                temperature REAL NOT NULL DEFAULT 0.7,
                max_tokens INTEGER,
                system_prompt TEXT
            )"""
        )
        c.execute(
            """CREATE TABLE IF NOT EXISTS sessions(
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL
            )"""
        )
        c.execute(
            """CREATE TABLE IF NOT EXISTS messages(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            )"""
        )
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id)"
        )
        row = c.execute("SELECT COUNT(*) FROM settings WHERE id = 1").fetchone()[0]
        if row == 0:
            s = DEFAULT_SETTINGS
            c.execute(
                """INSERT INTO settings(id, base_url, api_key, model, temperature, max_tokens, system_prompt)
                   VALUES (1, ?, ?, ?, ?, ?, ?)""",
                (s["base_url"], s["api_key"], s["model"], s["temperature"], s["max_tokens"], s["system_prompt"]),
            )
            log.info("settings seeded: %s", s["base_url"])


# ---------------- settings ----------------
def get_settings() -> dict:
    with get_conn() as c:
        row = c.execute("SELECT * FROM settings WHERE id = 1").fetchone()
    if row is None:
        return dict(DEFAULT_SETTINGS)
    out = dict(row)
    if out.get("max_tokens") is None:
        out["max_tokens"] = None
    return out


def update_settings(settings: dict) -> None:
    with get_conn() as c:
        c.execute(
            """UPDATE settings SET base_url=?, api_key=?, model=?, temperature=?, max_tokens=?, system_prompt=?
               WHERE id = 1""",
            (
                settings["base_url"],
                settings.get("api_key", ""),
                settings["model"],
                settings.get("temperature", 0.7),
                settings.get("max_tokens"),
                settings.get("system_prompt", ""),
            ),
        )


# ---------------- sessions ----------------
def create_session(session_id: str | None = None, title: str = "새 대화") -> str:
    sid = session_id or uuid.uuid4().hex
    with get_conn() as c:
        c.execute(
            "INSERT OR IGNORE INTO sessions(id, title, created_at) VALUES (?, ?, ?)",
            (sid, title, time.strftime("%Y-%m-%d %H:%M:%S")),
        )
    return sid


def get_session(session_id: str) -> sqlite3.Row | None:
    with get_conn() as c:
        return c.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()


def list_sessions() -> list[dict]:
    with get_conn() as c:
        rows = c.execute(
            """SELECT s.id, s.title, s.created_at,
                      (SELECT COUNT(*) FROM messages m WHERE m.session_id = s.id) AS message_count,
                      (SELECT content FROM messages m WHERE m.session_id = s.id
                        ORDER BY m.id DESC LIMIT 1) AS last_message
               FROM sessions s ORDER BY s.created_at DESC, s.id DESC"""
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------- messages ----------------
def add_message(session_id: str, role: str, content: str) -> int:
    with get_conn() as c:
        cur = c.execute(
            "INSERT INTO messages(session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            (session_id, role, content, time.strftime("%Y-%m-%d %H:%M:%S")),
        )
        return int(cur.lastrowid)


def list_messages(session_id: str) -> list[dict]:
    with get_conn() as c:
        rows = c.execute(
            "SELECT id, role, content, created_at FROM messages WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()
    return [dict(r) for r in rows]