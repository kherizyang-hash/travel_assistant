"""用户注册、登录与 JWT 鉴权。"""

import logging
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
import bcrypt
from pydantic import BaseModel, EmailStr, Field

from app_paths import PREFERENCES_DB

logger = logging.getLogger(__name__)

DB_TIMEOUT = 10.0
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_DAYS = 7

security = HTTPBearer(auto_error=False)


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: str
    email: str


class UserInfo(BaseModel):
    user_id: str
    email: str


def _get_jwt_secret() -> str:
    import os

    secret = os.getenv("JWT_SECRET")
    if secret:
        return secret
    if os.getenv("ENV") == "production":
        raise HTTPException(
            status_code=503,
            detail="服务未配置 JWT_SECRET，请在环境变量中设置",
        )
    logger.warning("JWT_SECRET 未配置，使用开发默认值（生产环境务必设置）")
    return "dev-insecure-change-me"


def _get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(PREFERENCES_DB, timeout=DB_TIMEOUT)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    return conn


def init_auth_schema() -> None:
    with _get_connection():
        pass


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


def create_access_token(user_id: str, email: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(days=ACCESS_TOKEN_EXPIRE_DAYS)
    payload = {"sub": user_id, "email": email, "exp": expire}
    return jwt.encode(payload, _get_jwt_secret(), algorithm=ALGORITHM)


def register_user(email: str, password: str) -> UserInfo:
    user_id = str(uuid.uuid4())
    password_hash = hash_password(password)
    now = datetime.now(timezone.utc).isoformat()
    try:
        with _get_connection() as conn:
            conn.execute(
                "INSERT INTO users (id, email, password_hash, created_at) VALUES (?, ?, ?, ?)",
                (user_id, email.lower(), password_hash, now),
            )
            conn.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="该邮箱已注册")
    except sqlite3.Error as exc:
        logger.error("注册失败: %s", exc)
        raise HTTPException(status_code=500, detail="注册失败")
    return UserInfo(user_id=user_id, email=email.lower())


def authenticate_user(email: str, password: str) -> Optional[UserInfo]:
    with _get_connection() as conn:
        row = conn.execute(
            "SELECT id, email, password_hash FROM users WHERE email = ?",
            (email.lower(),),
        ).fetchone()
    if not row or not verify_password(password, row["password_hash"]):
        return None
    return UserInfo(user_id=row["id"], email=row["email"])


def decode_token(token: str) -> UserInfo:
    try:
        payload = jwt.decode(token, _get_jwt_secret(), algorithms=[ALGORITHM])
        user_id = payload.get("sub")
        email = payload.get("email")
        if not user_id or not email:
            raise HTTPException(status_code=401, detail="无效令牌")
        return UserInfo(user_id=user_id, email=email)
    except JWTError as exc:
        raise HTTPException(status_code=401, detail="无效或已过期的令牌") from exc


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> UserInfo:
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未登录",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return decode_token(credentials.credentials)
