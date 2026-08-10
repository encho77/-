"""
database.py
타임캡슐 봇의 SQLite 데이터베이스 접근 모듈.

- 봇이 재시작되어도 데이터가 사라지지 않도록 파일 기반 SQLite를 사용한다.
- 모든 쿼리는 parameterized query로 작성하여 SQL Injection을 방지한다.
- threading.Lock으로 동시 쓰기 작업을 직렬화하여 race condition을 방지한다.
- Render의 Persistent Disk를 사용할 경우, 환경변수 DATABASE_PATH로 DB 파일 경로를 지정할 수 있다.
  (예: DATABASE_PATH=/data/database.db)
  환경변수가 없으면 로컬 실행 파일과 같은 위치의 database.db를 사용한다.
"""

import os
import sqlite3
import threading
from contextlib import contextmanager

# Render의 Persistent Disk 등을 사용할 경우 DATABASE_PATH 환경변수로 경로 지정 가능
DB_PATH = os.getenv("DATABASE_PATH", "database.db")

# 여러 코루틴이 동시에 DB에 접근하더라도 안전하게 처리하기 위한 락
_lock = threading.Lock()


@contextmanager
def _get_connection():
    """매 호출마다 새 커넥션을 열고, 정상 종료 시 commit, 항상 close 한다."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    """봇 시작 시 호출. 테이블/인덱스가 없으면 자동으로 생성한다."""
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

    with _lock, _get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS capsules (
                capsule_id      INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id         TEXT NOT NULL,
                username        TEXT NOT NULL,
                message         TEXT NOT NULL,
                created_at      TEXT NOT NULL,  -- ISO 8601 (UTC)
                delivery_at     TEXT NOT NULL,  -- ISO 8601 (UTC)
                delivered       INTEGER NOT NULL DEFAULT 0,
                delivered_at    TEXT,
                cancelled       INTEGER NOT NULL DEFAULT 0,
                delivery_failed INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_capsules_user_id ON capsules(user_id)")
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_capsules_pending
            ON capsules(delivered, cancelled, delivery_failed, delivery_at)
            """
        )


def create_capsule(user_id: str, username: str, message: str, created_at: str, delivery_at: str) -> int:
    """새 타임캡슐을 저장하고 생성된 capsule_id를 반환한다."""
    with _lock, _get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO capsules (user_id, username, message, created_at, delivery_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, username, message, created_at, delivery_at),
        )
        return cursor.lastrowid


def get_user_capsules(user_id: str) -> list[dict]:
    """특정 사용자가 만든 모든 타임캡슐을 도착일 순으로 반환한다."""
    with _lock, _get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM capsules WHERE user_id = ? ORDER BY delivery_at ASC",
            (user_id,),
        ).fetchall()
        return [dict(row) for row in rows]


def get_capsule_by_id(capsule_id: int) -> dict | None:
    """capsule_id로 단일 타임캡슐을 조회한다."""
    with _lock, _get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM capsules WHERE capsule_id = ?",
            (capsule_id,),
        ).fetchone()
        return dict(row) if row else None


def count_active_capsules(user_id: str) -> int:
    """아직 전송/취소/실패되지 않은(대기 중인) 타임캡슐 개수를 반환한다. (개수 제한용)"""
    with _lock, _get_connection() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS cnt FROM capsules
            WHERE user_id = ? AND delivered = 0 AND cancelled = 0 AND delivery_failed = 0
            """,
            (user_id,),
        ).fetchone()
        return row["cnt"]


def get_pending_capsules(now_utc_iso: str) -> list[dict]:
    """delivered=0, cancelled=0, delivery_failed=0 이면서 delivery_at이 지난 타임캡슐 목록을 반환한다."""
    with _lock, _get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM capsules
            WHERE delivered = 0 AND cancelled = 0 AND delivery_failed = 0 AND delivery_at <= ?
            ORDER BY delivery_at ASC
            """,
            (now_utc_iso,),
        ).fetchall()
        return [dict(row) for row in rows]


def mark_delivered(capsule_id: int, delivered_at: str) -> None:
    """전송 성공 시 delivered=1로 갱신한다. (delivered=0 조건으로 중복 전송 방지)"""
    with _lock, _get_connection() as conn:
        conn.execute(
            """
            UPDATE capsules SET delivered = 1, delivered_at = ?
            WHERE capsule_id = ? AND delivered = 0
            """,
            (delivered_at, capsule_id),
        )


def mark_delivery_failed(capsule_id: int) -> None:
    """DM 차단(Forbidden) 등 영구적으로 전송 불가능한 경우 재시도를 멈추기 위해 표시한다."""
    with _lock, _get_connection() as conn:
        conn.execute(
            "UPDATE capsules SET delivery_failed = 1 WHERE capsule_id = ? AND delivered = 0",
            (capsule_id,),
        )


def cancel_capsule(capsule_id: int, user_id: str) -> bool:
    """본인 소유이면서 아직 전송/취소되지 않은 타임캡슐만 취소한다. 성공 시 True 반환."""
    with _lock, _get_connection() as conn:
        cursor = conn.execute(
            """
            UPDATE capsules SET cancelled = 1
            WHERE capsule_id = ? AND user_id = ? AND delivered = 0 AND cancelled = 0
            """,
            (capsule_id, user_id),
        )
        return cursor.rowcount > 0
