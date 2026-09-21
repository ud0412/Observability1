"""
frontend SQLite DB — 모델 설정(다중) / 채팅 세션 / 메시지 저장.

파일: FRONTEND_DB 환경변수 (기본 /data/frontend.db)

- models   : 여러 개 저장 가능한 모델 설정 (name, base_url, api_key, ...)
- settings : 단일 행 — active_model(현재 선택된 모델 이름) + 구버전 설정 잔재
- sessions / messages : 채팅 이력
"""
import logging
import os
import sqlite3
import time
import uuid

log = logging.getLogger("frontend")

DB_PATH = os.environ.get("FRONTEND_DB", "/data/frontend.db")

DEFAULT_SETTINGS = {
    "name": "기본 모델",
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


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


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
                system_prompt TEXT,
                active_model TEXT
            )"""
        )
        c.execute(
            """CREATE TABLE IF NOT EXISTS models(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                base_url TEXT NOT NULL,
                api_key TEXT NOT NULL DEFAULT '',
                model TEXT NOT NULL,
                temperature REAL NOT NULL DEFAULT 0.7,
                max_tokens INTEGER,
                system_prompt TEXT,
                created_at TEXT NOT NULL
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
        c.execute("CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id)")

        # 구버전 DB 마이그레이션: settings에 active_model 컬럼 추가
        cols = [r[1] for r in c.execute("PRAGMA table_info(settings)")]
        if "active_model" not in cols:
            c.execute("ALTER TABLE settings ADD COLUMN active_model TEXT")
            log.info("migrated: settings.active_model column added")

        # settings 시드 (id=1)
        if c.execute("SELECT COUNT(*) FROM settings WHERE id = 1").fetchone()[0] == 0:
            s = DEFAULT_SETTINGS
            c.execute(
                """INSERT INTO settings(id, base_url, api_key, model, temperature, max_tokens, system_prompt, active_model)
                   VALUES (1, ?, ?, ?, ?, ?, ?, ?)""",
                (s["base_url"], s["api_key"], s["model"], s["temperature"], s["max_tokens"], s["system_prompt"], None),
            )

        # models 시드/마이그레이션: 기존 settings 행을 "기본 모델"로 복사
        if c.execute("SELECT COUNT(*) FROM models").fetchone()[0] == 0:
            old = c.execute(
                "SELECT base_url, api_key, model, temperature, max_tokens, system_prompt FROM settings WHERE id = 1"
            ).fetchone()
            vals = tuple(old) if old else (
                DEFAULT_SETTINGS["base_url"], DEFAULT_SETTINGS["api_key"], DEFAULT_SETTINGS["model"],
                DEFAULT_SETTINGS["temperature"], DEFAULT_SETTINGS["max_tokens"], DEFAULT_SETTINGS["system_prompt"],
            )
            c.execute(
                """INSERT INTO models(name, base_url, api_key, model, temperature, max_tokens, system_prompt, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                ("기본 모델", *vals, _now()),
            )
            c.execute("UPDATE settings SET active_model = '기본 모델' WHERE id = 1")
            log.info("models seeded: '기본 모델' from legacy settings row")
        else:
            active = c.execute("SELECT active_model FROM settings WHERE id = 1").fetchone()[0]
            if not active:
                first = c.execute("SELECT name FROM models ORDER BY id LIMIT 1").fetchone()
                c.execute("UPDATE settings SET active_model = ? WHERE id = 1", (first["name"] if first else None,))


def _normalize(row: sqlite3.Row) -> dict:
    out = dict(row)
    if out.get("max_tokens") == 0:  # 프런트 입력상의 0 방지
        out["max_tokens"] = None
    return out


# ---------------- models ----------------
def get_active_model_name() -> str | None:
    with get_conn() as c:
        row = c.execute("SELECT active_model FROM settings WHERE id = 1").fetchone()
    return row["active_model"] if row else None


def get_active_model() -> dict:
    """현재 활성 모델 설정 (없으면 기본값 폴백)."""
    name = get_active_model_name()
    if name:
        with get_conn() as c:
            row = c.execute("SELECT * FROM models WHERE name = ?", (name,)).fetchone()
        if row:
            return _normalize(row)
    out = dict(DEFAULT_SETTINGS)
    return out


def get_model_by_id(model_id: int) -> dict | None:
    with get_conn() as c:
        row = c.execute("SELECT * FROM models WHERE id = ?", (model_id,)).fetchone()
    return _normalize(row) if row else None


def list_models() -> list[dict]:
    active = get_active_model_name()
    with get_conn() as c:
        rows = c.execute("SELECT * FROM models ORDER BY id").fetchall()
    return [{**_normalize(r), "is_active": r["name"] == active} for r in rows]


def create_model(name: str, base_url: str, api_key: str, model: str, temperature: float,
                 max_tokens: int | None, system_prompt: str) -> int:
    name = (name or "").strip() or model or "모델"
    with get_conn() as c:
        try:
            cur = c.execute(
                """INSERT INTO models(name, base_url, api_key, model, temperature, max_tokens, system_prompt, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (name, base_url, api_key, model, temperature, max_tokens, system_prompt, _now()),
            )
            return int(cur.lastrowid)
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"이미 같은 이름의 모델이 있습니다: {name}") from exc


def update_model(model_id: int, name: str, base_url: str, api_key: str, model: str,
                 temperature: float, max_tokens: int | None, system_prompt: str) -> None:
    with get_conn() as c:
        old = c.execute("SELECT name FROM models WHERE id = ?", (model_id,)).fetchone()
        if old is None:
            raise ValueError("모델을 찾을 수 없습니다")
        name = (name or "").strip() or model or old["name"]
        try:
            c.execute(
                """UPDATE models SET name=?, base_url=?, api_key=?, model=?, temperature=?, max_tokens=?, system_prompt=?
                   WHERE id = ?""",
                (name, base_url, api_key, model, temperature, max_tokens, system_prompt, model_id),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"이미 같은 이름의 모델이 있습니다: {name}") from exc
        # 이름이 바뀌었으면 active_model 참조도 갱신
        if old["name"] == get_active_model_name():
            c.execute("UPDATE settings SET active_model = ? WHERE id = 1", (name,))


def delete_model(model_id: int) -> None:
    with get_conn() as c:
        row = c.execute("SELECT name FROM models WHERE id = ?", (model_id,)).fetchone()
        if row is None:
            return
        name = row["name"]
        c.execute("DELETE FROM models WHERE id = ?", (model_id,))
        if get_active_model_name() == name:
            first = c.execute("SELECT name FROM models ORDER BY id LIMIT 1").fetchone()
            c.execute("UPDATE settings SET active_model = ? WHERE id = 1", (first["name"] if first else None,))


def set_active_model(model_id: int) -> dict:
    with get_conn() as c:
        row = c.execute("SELECT name FROM models WHERE id = ?", (model_id,)).fetchone()
        if row is None:
            raise ValueError("모델을 찾을 수 없습니다")
        c.execute("UPDATE settings SET active_model = ? WHERE id = 1", (row["name"],))
    active = get_active_model()
    log.info("active model set: %s (%s @ %s)", active["name"], active["model"], active["base_url"])
    return active


# ---------------- sessions ----------------
def create_session(session_id: str | None = None, title: str = "새 대화") -> str:
    sid = session_id or uuid.uuid4().hex
    with get_conn() as c:
        c.execute(
            "INSERT OR IGNORE INTO sessions(id, title, created_at) VALUES (?, ?, ?)",
            (sid, title, _now()),
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
            (session_id, role, content, _now()),
        )
        return int(cur.lastrowid)


def list_messages(session_id: str) -> list[dict]:
    with get_conn() as c:
        rows = c.execute(
            "SELECT id, role, content, created_at FROM messages WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()
    return [dict(r) for r in rows]