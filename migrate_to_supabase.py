"""
migrate_to_supabase.py

기존 로컬 SQLite(database.db)에 저장되어 있던 타임캡슐 데이터를 Supabase PostgreSQL로
1회성으로 옮기는 독립 스크립트입니다.

⚠️ 이 스크립트는 봇 실행(main.py)과는 별개로, 로컬 환경에서 필요할 때만 수동으로 실행하세요.
⚠️ 로컬 SQLite 파일의 데이터를 절대 삭제하지 않습니다. (읽기만 함)
⚠️ Supabase의 기존 데이터도 삭제하지 않습니다. (INSERT만 수행)

사용법:
    # 1) 환경변수 설정
    export DATABASE_URL="postgresql://postgres.xxxx:[PASSWORD]@aws-0-xxxx.pooler.supabase.com:5432/postgres"

    # 2) 필요 패키지 설치 (asyncpg는 requirements.txt에 이미 포함)
    pip install -r requirements.txt

    # 3) 실행 (기본적으로 같은 폴더의 database.db를 읽음)
    python migrate_to_supabase.py

    # 다른 경로의 SQLite 파일을 사용하려면:
    python migrate_to_supabase.py /path/to/old_database.db
"""

import os
import sys
import asyncio
import sqlite3
from datetime import datetime, timezone

import asyncpg


def parse_iso(value: str | None) -> datetime | None:
    """기존 SQLite에 저장된 ISO 8601 UTC 문자열을 timezone-aware datetime으로 변환한다."""
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


async def migrate(sqlite_path: str, database_url: str) -> None:
    if not os.path.exists(sqlite_path):
        print(f"❌ SQLite 파일을 찾을 수 없습니다: {sqlite_path}")
        return

    # 1) 기존 SQLite에서 데이터 읽기 (읽기 전용, 절대 수정/삭제하지 않음)
    conn = sqlite3.connect(sqlite_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT * FROM capsules").fetchall()
    except sqlite3.OperationalError as e:
        print(f"❌ 기존 SQLite에서 capsules 테이블을 읽을 수 없습니다: {e}")
        conn.close()
        return
    finally:
        conn.close()

    if not rows:
        print("ℹ️ 이전할 타임캡슐 데이터가 없습니다. (capsules 테이블이 비어있음)")
        return

    print(f"📦 이전할 타임캡슐 {len(rows)}개를 발견했습니다.")
    confirm = input("Supabase로 이전을 진행할까요? (y/n): ").strip().lower()
    if confirm != "y":
        print("취소되었습니다.")
        return

    # 2) Supabase에 연결 후 INSERT (capsule_id는 새로 발급되도록 지정하지 않음)
    pool = await asyncpg.create_pool(dsn=database_url, min_size=1, max_size=2, statement_cache_size=0)
    migrated = 0
    failed = 0
    try:
        async with pool.acquire() as db_conn:
            # 테이블이 없으면 생성 (기존 데이터 삭제 없이 안전하게)
            await db_conn.execute(
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

            for row in rows:
                try:
                    await db_conn.execute(
                        """
                        INSERT INTO time_capsules
                            (user_id, username, message, created_at, delivery_at,
                             delivered, delivered_at, cancelled, delivery_failed)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                        """,
                        str(row["user_id"]),
                        row["username"],
                        row["message"],
                        parse_iso(row["created_at"]),
                        parse_iso(row["delivery_at"]),
                        bool(row["delivered"]),
                        parse_iso(row["delivered_at"]),
                        bool(row["cancelled"]) if "cancelled" in row.keys() else False,
                        bool(row["delivery_failed"]) if "delivery_failed" in row.keys() else False,
                    )
                    migrated += 1
                except Exception as e:
                    failed += 1
                    print(f"⚠️ capsule_id={row['capsule_id']} 이전 실패: {e}")
    finally:
        await pool.close()

    print(f"✅ 이전 완료: 성공 {migrated}건, 실패 {failed}건")


if __name__ == "__main__":
    sqlite_path_arg = sys.argv[1] if len(sys.argv) > 1 else "database.db"

    database_url_env = os.getenv("DATABASE_URL")
    if not database_url_env:
        print("❌ 환경변수 DATABASE_URL이 설정되지 않았습니다.")
        print('   예: export DATABASE_URL="postgresql://postgres.xxxx:[PASSWORD]@..."')
        sys.exit(1)

    asyncio.run(migrate(sqlite_path_arg, database_url_env))
