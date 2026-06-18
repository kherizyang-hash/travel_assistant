"""对话会话元数据（与 preferences 同库）。"""

import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Optional

from app_paths import PREFERENCES_DB

logger = logging.getLogger(__name__)

DB_TIMEOUT = 10.0
DEFAULT_TITLE = "新对话"
TITLE_MAX_LEN = 20


def _get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(PREFERENCES_DB, timeout=DB_TIMEOUT)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            thread_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '新对话',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_conv_user
        ON conversations(user_id, updated_at DESC)
    """)
    conn.commit()
    return conn


def init_conversation_schema() -> None:
    with _get_connection():
        pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_title_from_message(message: str) -> str:
    text = message.strip().replace("\n", " ")
    if not text:
        return DEFAULT_TITLE
    return text[:TITLE_MAX_LEN] + ("…" if len(text) > TITLE_MAX_LEN else "")


def create_conversation(user_id: str, thread_id: Optional[str] = None) -> dict:
    tid = thread_id or str(uuid.uuid4())
    now = _now_iso()
    with _get_connection() as conn:
        conn.execute(
            """
            INSERT INTO conversations (thread_id, user_id, title, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (tid, user_id, DEFAULT_TITLE, now, now),
        )
        conn.commit()
    return {
        "thread_id": tid,
        "title": DEFAULT_TITLE,
        "created_at": now,
        "updated_at": now,
    }


def list_conversations(user_id: str) -> list[dict]:
    with _get_connection() as conn:
        rows = conn.execute(
            """
            SELECT thread_id, title, created_at, updated_at
            FROM conversations
            WHERE user_id = ?
            ORDER BY updated_at DESC
            """,
            (user_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_conversation(thread_id: str, user_id: str) -> Optional[dict]:
    with _get_connection() as conn:
        row = conn.execute(
            """
            SELECT thread_id, user_id, title, created_at, updated_at
            FROM conversations
            WHERE thread_id = ? AND user_id = ?
            """,
            (thread_id, user_id),
        ).fetchone()
    return dict(row) if row else None


def require_conversation(thread_id: str, user_id: str) -> dict:
    conv = get_conversation(thread_id, user_id)
    if not conv:
        raise PermissionError("对话不存在或无权访问")
    return conv


def touch_conversation(thread_id: str, user_id: str, message: Optional[str] = None) -> None:
    now = _now_iso()
    with _get_connection() as conn:
        if message:
            conv = conn.execute(
                "SELECT title FROM conversations WHERE thread_id = ? AND user_id = ?",
                (thread_id, user_id),
            ).fetchone()
            if conv and conv["title"] == DEFAULT_TITLE:
                conn.execute(
                    """
                    UPDATE conversations
                    SET updated_at = ?, title = ?
                    WHERE thread_id = ? AND user_id = ?
                    """,
                    (now, make_title_from_message(message), thread_id, user_id),
                )
            else:
                conn.execute(
                    """
                    UPDATE conversations SET updated_at = ?
                    WHERE thread_id = ? AND user_id = ?
                    """,
                    (now, thread_id, user_id),
                )
        else:
            conn.execute(
                """
                UPDATE conversations SET updated_at = ?
                WHERE thread_id = ? AND user_id = ?
                """,
                (now, thread_id, user_id),
            )
        conn.commit()


def delete_conversation(thread_id: str, user_id: str) -> bool:
    with _get_connection() as conn:
        cur = conn.execute(
            "DELETE FROM conversations WHERE thread_id = ? AND user_id = ?",
            (thread_id, user_id),
        )
        conn.commit()
        return cur.rowcount > 0
