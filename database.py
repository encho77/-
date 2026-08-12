"""
database.py
타임캡슐 봇의 Supabase(PostgreSQL) 데이터베이스 접근 모듈.

[변경 이력 - SQLite → Supabase PostgreSQL 전환]
- 기존: sqlite3 + threading.Lock, 동기 함수를 asyncio.to_thread로 감싸서 사용
- 변경: asyncpg 커넥션 풀을 이용한 완전 비동기(native async) 방식
- 이유:
  1) discord.py는 asyncio 기반이므로 네이티브 async 드라이버가 이벤트 루프를 막지 않음
  2) supabase-py SDK(PostgREST 경유, 내부적으로 동기 HTTP)보다 지연이 적고 직접 SQL 제어 가능
  3) Render가 재배포/재시작되어도 Supabase에 저장된 데이터는 그대로 유지됨

[연결 방식]
환경변수 DATABASE_URL 에 Supabase가 제공하는 PostgreSQL 연결 문자열(URI)을 넣는다.
※ 반드시 "Session pooler" 연결 문자열(포트 5432)을 사용할 것을 권장한다.
   Supabase의 "Direct connection"은 IPv6 전용이라 Render(IPv4)에서 연결이 안 될 수 있다.
   예: postgresql://postgres.xxxxxxxx:[YOUR-PASSWORD]@aws-0-ap-northeast-2.pooler.supabase.com:5432/postgres

[테이블]
time_capsules 테이블을 사용한다. (컬럼명은 기존 SQLite 스키마와 동일하게 유지 - 최소 변경 원칙)
Supabase SQL Editor에서 실행할 CREATE TABLE 스크립트는 README.md 및 supabase_schema.sql 참고.
※ 이 모듈은 절대로 DROP TABLE / DELETE FROM 등 데이터를 삭제하는 쿼리를 실행하지 않는다.
"""

import os
import asyncio
import logging
from datetime import datetime

import asyncpg

logger = logging.getLogger("time_capsule_bot")

# Supabase가 제공하는 PostgreSQL 연결 문자열 (Session Pooler 권장, 포트 5432)
# 절대 코드에 하드코딩하지 않고 Render Environment Variables에서 가져온다.
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    # 여기서 즉시 종료(raise)하지 않는다.
    # Supabase 연결 문제로 봇 전체(로그인/Slash Command 등)가 죽지 않도록
    # 실제 연결 시도는 get_pool()에서 지연(lazy)하여 처리하고, 각 명령어의
    # 기존 try/except가 이 오류를 잡아 사용자에게 안내 메시지를 보여준다.
    logger.warning(
        "환경변수 DATABASE_URL이 설정되지 않았습니다. "
        "Supabase 연결 문자열을 Render Environment Variables에 등록해주세요. "
        "(타임캡슐 생성/조회 기능이 정상 동작하지 않습니다)"
    )

# ─────────────────────────────────────────────
# 커넥션 풀 (지연 생성, 재연결 지원)
# ─────────────────────────────────────────────
_pool: asyncpg.Pool | None = None
_pool_lock = asyncio.Lock()


async def get_pool() -> asyncpg.Pool:
    """
    커넥션 풀을 반환한다. 아직 생성되지 않았다면 생성을 시도한다.
    연결 실패 시 예외를 그대로 상위로 전파한다. (호출부의 try/except에서 처리)
    이미 풀이 존재하면 재사용하므로, 최초 1회 실패 후 Supabase가 복구되면
    다음 호출에서 다시 연결을 시도하게 된다.
    """
    global _pool

    if _pool is not None:
        return _pool

    async with _pool_lock:
        if _pool is None:  # 락 획득 후 재확인 (동시 접근 시 중복 생성 방지)
            if not DATABASE_URL:
                raise RuntimeError(
                    "DATABASE_URL 환경변수가 설정되지 않아 Supabase에 연결할 수 없습니다."
                )
            _pool = await asyncpg.create_pool(
                dsn=DATABASE_URL,
                min_size=1,
                max_size=5,
                command_timeout=15,
                # PgBouncer(Transaction 모드) 사용 시 prepared statement 충돌을 막기 위한 방어적 설정.
                # Session Pooler/직접 연결에서도 문제 없이 동작한다.
                statement_cache_size=0,
            )
            logger.info("Supabase(PostgreSQL) 커넥션 풀 생성 완료")

    return _pool


async def close_pool() -> None:
    """봇 종료 시 커넥션 풀을 안전하게 닫는다."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        logger.info("Supabase(PostgreSQL) 커넥션 풀을 종료했습니다.")


async def init_db() -> None:
    """
    봇 시작 시 호출. 테이블/인덱스가 없으면 생성한다. (IF NOT EXISTS만 사용 - 기존 데이터 절대 삭제하지 않음)
    이미 테이블/데이터가 존재하면 아무 영향을 주지 않는다.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS time_capsules (
                capsule_id      BIGSERIAL PRIMARY KEY,
                user_id         TEXT NOT NULL,
                username        TEXT NOT NULL,
                message         TEXT NOT NULL,
                created_at      TIMESTAMPTZ NOT NULL,
                delivery_at     TIMESTAMPTZ NOT NULL,
                delivered       BOOLEAN NOT NULL DEFAULT FALSE,
                delivered_at    TIMESTAMPTZ,
                cancelled       BOOLEAN NOT NULL DEFAULT FALSE,
                delivery_failed BOOLEAN NOT NULL DEFAULT FALSE
            )
            """
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_time_capsules_user_id ON time_capsules(user_id)"
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_time_capsules_pending
            ON time_capsules(delivered, cancelled, delivery_failed, delivery_at)
            """
        )


async def create_capsule(
    user_id: str, username: str, message: str, created_at: datetime, delivery_at: datetime
) -> int:
    """새 타임캡슐을 저장하고 생성된 capsule_id를 반환한다. (parameterized query로 SQL Injection 방지)"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO time_capsules (user_id, username, message, created_at, delivery_at)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING capsule_id
            """,
            user_id, username, message, created_at, delivery_at,
        )
        return row["capsule_id"]


async def get_user_capsules(user_id: str) -> list[dict]:
    """특정 사용자가 만든 모든 타임캡슐을 도착일 순으로 반환한다."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM time_capsules WHERE user_id = $1 ORDER BY delivery_at ASC",
            user_id,
        )
        return [dict(row) for row in rows]


async def get_capsule_by_id(capsule_id: int) -> dict | None:
    """capsule_id로 단일 타임캡슐을 조회한다."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM time_capsules WHERE capsule_id = $1", capsule_id
        )
        return dict(row) if row else None


async def count_active_capsules(user_id: str) -> int:
    """아직 전송/취소/실패되지 않은(대기 중인) 타임캡슐 개수를 반환한다. (개수 제한용)"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        cnt = await conn.fetchval(
            """
            SELECT COUNT(*) FROM time_capsules
            WHERE user_id = $1 AND delivered = FALSE AND cancelled = FALSE AND delivery_failed = FALSE
            """,
            user_id,
        )
        return cnt


async def get_pending_capsules(now_utc: datetime) -> list[dict]:
    """delivered=false, cancelled=false, delivery_failed=false 이면서 delivery_at이 지난 타임캡슐 목록을 반환한다."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT * FROM time_capsules
            WHERE delivered = FALSE AND cancelled = FALSE AND delivery_failed = FALSE AND delivery_at <= $1
            ORDER BY delivery_at ASC
            """,
            now_utc,
        )
        return [dict(row) for row in rows]


async def mark_delivered(capsule_id: int, delivered_at: datetime) -> None:
    """전송 성공 시 delivered=true로 갱신한다. (delivered=false 조건으로 원자적 업데이트 → 중복 전송 방지)"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE time_capsules SET delivered = TRUE, delivered_at = $1
            WHERE capsule_id = $2 AND delivered = FALSE
            """,
            delivered_at, capsule_id,
        )


async def mark_delivery_failed(capsule_id: int) -> None:
    """DM 차단(Forbidden) 등 영구적으로 전송 불가능한 경우 재시도를 멈추기 위해 표시한다."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE time_capsules SET delivery_failed = TRUE WHERE capsule_id = $1 AND delivered = FALSE",
            capsule_id,
        )


async def cancel_capsule(capsule_id: int, user_id: str) -> bool:
    """본인 소유이면서 아직 전송/취소되지 않은 타임캡슐만 취소한다. 성공 시 True 반환."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE time_capsules SET cancelled = TRUE
            WHERE capsule_id = $1 AND user_id = $2 AND delivered = FALSE AND cancelled = FALSE
            """,
            capsule_id, user_id,
        )
        # asyncpg의 execute()는 "UPDATE 1" 같은 문자열을 반환한다. 마지막 숫자가 영향받은 row 수.
        affected = int(result.split()[-1])
        return affected > 0
