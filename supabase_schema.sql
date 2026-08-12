-- ─────────────────────────────────────────────
-- Supabase SQL Editor에서 1회 실행하세요.
-- (봇 코드도 시작 시 동일한 내용을 자동으로 실행하지만,
--  배포 전에 미리 테이블을 만들어두고 싶다면 이 스크립트를 사용하세요.)
--
-- 이 스크립트는 IF NOT EXISTS만 사용하므로 여러 번 실행해도 안전하며,
-- 기존 데이터를 삭제하거나 초기화하지 않습니다.
-- ─────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS time_capsules (
    capsule_id      BIGSERIAL PRIMARY KEY,
    user_id         TEXT NOT NULL,          -- Discord User ID (문자열로 저장)
    username        TEXT NOT NULL,          -- 생성 당시 사용자 이름 (표시용, 식별은 user_id로 함)
    message         TEXT NOT NULL,          -- 미래의 자신에게 남긴 메시지
    created_at      TIMESTAMPTZ NOT NULL,   -- 타임캡슐 생성 시각 (UTC로 저장)
    delivery_at     TIMESTAMPTZ NOT NULL,   -- 발송 예정 시각 (UTC로 저장)
    delivered       BOOLEAN NOT NULL DEFAULT FALSE,  -- 발송 완료 여부
    delivered_at    TIMESTAMPTZ,            -- 실제 발송된 시각
    cancelled       BOOLEAN NOT NULL DEFAULT FALSE,  -- 사용자가 취소했는지 여부
    delivery_failed BOOLEAN NOT NULL DEFAULT FALSE   -- DM 차단 등으로 영구 발송 실패했는지 여부
);

-- 사용자별 조회 성능을 위한 인덱스
CREATE INDEX IF NOT EXISTS idx_time_capsules_user_id ON time_capsules(user_id);

-- 백그라운드 발송 작업이 매 주기마다 조회하는 조건에 대한 인덱스
CREATE INDEX IF NOT EXISTS idx_time_capsules_pending
ON time_capsules(delivered, cancelled, delivery_failed, delivery_at);
