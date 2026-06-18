"""
用户偏好管理模块
使用 SQLite 持久化存储，以登录 user_id 为键。
"""

import logging
import sqlite3
import time
from datetime import datetime
from typing import Optional

from app_paths import PREFERENCES_DB, ensure_data_dirs

logger = logging.getLogger(__name__)

PREFERENCE_LABELS = {
    "hotel_brand": "酒店品牌",
    "hotel_quality": "酒店质量",
    "budget_range": "酒店预算",
    "departure_time": "出门时间",
    "transport_mode": "交通方式",
    "last_destination": "最近目的地",
    "travel_style": "旅行风格",
    "accommodation": "住宿要求",
    "cuisine_preference": "美食倾向",
    "room_view": "窗景偏好",
}

ALLOWED_PREFERENCE_KEYS = frozenset(PREFERENCE_LABELS.keys())

DB_PATH = PREFERENCES_DB
DB_TIMEOUT = 10.0
SAVE_RETRY_COUNT = 3
SAVE_RETRY_DELAY = 0.2


def init_preference_schema() -> None:
    ensure_data_dirs()
    with _get_connection():
        pass


def _get_connection() -> sqlite3.Connection:
    """获取数据库连接，首次运行时自动建表。"""
    ensure_data_dirs()
    conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_preferences (
            user_id TEXT NOT NULL,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, key)
        )
    """)
    conn.commit()
    return conn


def save_preference(user_id: str, key: str, value: str) -> bool:
    """保存或更新用户偏好，遇锁冲突时自动重试。"""
    for attempt in range(SAVE_RETRY_COUNT):
        try:
            with _get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO user_preferences (user_id, key, value, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(user_id, key) DO UPDATE SET
                        value = excluded.value,
                        updated_at = excluded.updated_at
                    """,
                    (user_id, key, value, datetime.now().isoformat()),
                )
                conn.commit()
            logger.info("偏好已写入 SQLite: user_id=%s, %s=%s", user_id, key, value)
            return True
        except sqlite3.OperationalError as exc:
            if "locked" in str(exc).lower() and attempt < SAVE_RETRY_COUNT - 1:
                logger.warning(
                    "数据库被占用，重试 %s/%s: user_id=%s, %s=%s",
                    attempt + 1,
                    SAVE_RETRY_COUNT,
                    user_id,
                    key,
                    value,
                )
                time.sleep(SAVE_RETRY_DELAY)
                continue
            logger.error(
                "偏好写入失败(数据库锁定): user_id=%s, %s=%s, error=%s",
                user_id,
                key,
                value,
                exc,
            )
            return False
        except sqlite3.Error as exc:
            logger.error(
                "偏好写入失败: user_id=%s, %s=%s, error=%s",
                user_id,
                key,
                value,
                exc,
            )
            return False
    return False


def delete_preference(user_id: str, key: str) -> bool:
    """删除单条偏好，不存在时返回 False。"""
    try:
        with _get_connection() as conn:
            cur = conn.execute(
                "DELETE FROM user_preferences WHERE user_id = ? AND key = ?",
                (user_id, key),
            )
            conn.commit()
        return cur.rowcount > 0
    except sqlite3.Error as exc:
        logger.error("偏好删除失败: user_id=%s, key=%s, error=%s", user_id, key, exc)
        return False


def delete_all_preferences(user_id: str) -> int:
    """删除用户全部偏好，返回删除条数。"""
    try:
        with _get_connection() as conn:
            cur = conn.execute(
                "DELETE FROM user_preferences WHERE user_id = ?",
                (user_id,),
            )
            conn.commit()
        return cur.rowcount
    except sqlite3.Error as exc:
        logger.error("偏好清空失败: user_id=%s, error=%s", user_id, exc)
        return 0


def get_preference(user_id: str, key: str) -> Optional[str]:
    """获取单个偏好值，不存在则返回 None。"""
    with _get_connection() as conn:
        row = conn.execute(
            "SELECT value FROM user_preferences WHERE user_id = ? AND key = ?",
            (user_id, key),
        ).fetchone()
    return row["value"] if row else None


def get_all_preferences(user_id: str) -> dict:
    """获取用户全部偏好，返回 {key: value} 字典。"""
    with _get_connection() as conn:
        rows = conn.execute(
            "SELECT key, value FROM user_preferences WHERE user_id = ?",
            (user_id,),
        ).fetchall()
    return {row["key"]: row["value"] for row in rows}


def list_all_users() -> list[str]:
    """列出所有有偏好记录的用户 ID（调试用）。"""
    with _get_connection() as conn:
        rows = conn.execute(
            "SELECT DISTINCT user_id FROM user_preferences ORDER BY user_id"
        ).fetchall()
    return [row["user_id"] for row in rows]


def get_preference_context(user_id: str) -> str:
    """返回格式化的偏好摘要，用于注入 System Prompt。"""
    prefs = get_all_preferences(user_id)
    if not prefs:
        logger.info("偏好注入: user_id=%s, keys=[]", user_id)
        return "（暂无已知偏好）"

    logger.info("偏好注入: user_id=%s, keys=%s", user_id, list(prefs.keys()))
    lines = ["## 用户已知偏好（来自数据库，回答偏好问题时仅引用以下内容）"]
    for key, value in prefs.items():
        label = PREFERENCE_LABELS.get(key, key)
        lines.append(f"- {label}：{value}")
    return "\n".join(lines)
