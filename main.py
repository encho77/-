"""
main.py
타임캡슐(Time Capsule) Discord 봇

기능 요약
- /타임캡슐      : 기간(1/3/6/9/12개월) 선택 후 메시지를 작성하여 타임캡슐 생성
- /내타임캡슐    : 내가 만든 타임캡슐 목록 확인
- /타임캡슐조회  : 특정 타임캡슐의 상세 상태 확인
- /타임캡슐취소  : 아직 도착하지 않은 타임캡슐 취소
- 도착 시간이 되면 백그라운드 작업이 DM으로 자동 전송 (봇 재시작에도 유지됨)

Render Web Service 배포용:
- Flask로 간단한 HTTP 서버 실행 (UptimeRobot이 주기적으로 핑을 보내 슬립 방지)
- 스레드에서 Flask 실행, 메인 스레드에서 봇 실행

DB 저장소: Supabase PostgreSQL (asyncpg 기반 완전 비동기 연결)
- 기존 SQLite 방식에서 전환됨. 자세한 내용은 database.py 상단 주석 참고.
- Render가 재배포/재시작되어도 타임캡슐 데이터는 Supabase에 그대로 유지된다.
"""

import os
import logging
import threading
from datetime import datetime, timezone, timedelta

import discord
from discord import app_commands
from discord.ext import commands, tasks
from dateutil.relativedelta import relativedelta
from flask import Flask

import database as db

# ─────────────────────────────────────────────
# 로그 설정
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("time_capsule_bot")

# ─────────────────────────────────────────────
# 환경변수
# ─────────────────────────────────────────────
TOKEN = os.getenv("TOKEN")
if not TOKEN:
    raise RuntimeError(
        "환경변수 TOKEN이 설정되지 않았습니다. Discord Bot Token을 환경변수로 설정해주세요."
    )

# Render Web Service에서는 PORT 환경변수가 자동 지정됨 (보통 10000)
# 로컬 테스트 시에는 5000번 포트 사용
PORT = int(os.getenv("PORT", 5000))

# 사용자 1인당 최대 대기 중(미전송) 타임캡슐 개수
MAX_CAPSULES_PER_USER = int(os.getenv("MAX_CAPSULES_PER_USER", "10"))

# 몇 초마다 전송 대상 타임캡슐을 확인할지
CHECK_INTERVAL_SECONDS = int(os.getenv("CHECK_INTERVAL_SECONDS", "30"))

MAX_MESSAGE_LENGTH = 1000

KST = timezone(timedelta(hours=9))

DURATION_OPTIONS = {
    "1개월": 1,
    "3개월": 3,
    "6개월": 6,
    "9개월": 9,
    "12개월": 12,
}


# ─────────────────────────────────────────────
# 시간 유틸리티 함수
# ─────────────────────────────────────────────
def now_kst() -> datetime:
    """현재 시각을 한국 시간(KST, timezone-aware)으로 반환한다."""
    return datetime.now(timezone.utc).astimezone(KST)


def to_kst_str(dt: datetime | None) -> str:
    """
    DB(asyncpg)에서 반환된 timezone-aware datetime을 'YYYY-MM-DD HH:MM' 형태의 KST 문자열로 변환한다.
    (Supabase 전환 이후: asyncpg가 timestamptz 컬럼을 Python datetime 객체로 그대로 반환하므로
     기존의 ISO 문자열 파싱 로직 대신 datetime 객체를 직접 받는다.)
    """
    if dt is None:
        return "-"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(KST).strftime("%Y-%m-%d %H:%M")


def remaining_str(delivery_at: datetime) -> str:
    """도착까지 남은 시간을 사람이 읽기 좋은 문자열로 반환한다."""
    dt = delivery_at
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = dt - datetime.now(timezone.utc)
    total_seconds = delta.total_seconds()
    if total_seconds <= 0:
        return "곧 도착 예정"
    days = delta.days
    hours = delta.seconds // 3600
    if days > 0:
        return f"{days}일 {hours}시간 남음"
    minutes = (delta.seconds % 3600) // 60
    return f"{hours}시간 {minutes}분 남음"


# ─────────────────────────────────────────────
# 메시지 입력 Modal
# ─────────────────────────────────────────────
class TimeCapsuleModal(discord.ui.Modal):
    def __init__(self, months: int):
        super().__init__(title="미래의 나에게")
        self.months = months
        self.message_input = discord.ui.TextInput(
            label="미래의 자신에게 남길 메시지를 입력하세요.",
            style=discord.TextStyle.paragraph,
            placeholder="안녕 미래의 나! 잘 지내고 있니?",
            max_length=MAX_MESSAGE_LENGTH,
            required=True,
        )
        self.add_item(self.message_input)

    async def on_submit(self, interaction: discord.Interaction):
        message_text = self.message_input.value.strip()

        # 빈 메시지 방지 (필수 항목이지만 공백만 입력한 경우까지 방어)
        if not message_text:
            await interaction.response.send_message(
                "❌ 메시지를 입력해주세요. 빈 메시지는 저장할 수 없습니다.",
                ephemeral=True,
            )
            return

        if len(message_text) > MAX_MESSAGE_LENGTH:
            await interaction.response.send_message(
                f"❌ 메시지가 너무 깁니다. 최대 {MAX_MESSAGE_LENGTH}자까지 입력할 수 있습니다.",
                ephemeral=True,
            )
            return

        # 사용자별 개수 제한 확인 (Supabase 조회 - asyncpg는 네이티브 async이므로 to_thread 불필요)
        try:
            active_count = await db.count_active_capsules(str(interaction.user.id))
        except Exception:
            logger.exception("타임캡슐 개수 확인 중 DB 오류 발생 (Supabase 연결 상태 확인 필요)")
            await interaction.response.send_message(
                "❌ 데이터베이스 오류가 발생했습니다. 잠시 후 다시 시도해주세요.",
                ephemeral=True,
            )
            return

        if active_count >= MAX_CAPSULES_PER_USER:
            await interaction.response.send_message(
                f"❌ 최대 {MAX_CAPSULES_PER_USER}개의 타임캡슐만 동시에 보관할 수 있습니다.\n"
                f"기존 타임캡슐이 도착하거나 취소된 뒤 다시 시도해주세요.",
                ephemeral=True,
            )
            return

        # 실제 달(month) 기준으로 도착 시간 계산 (KST 기준)
        created_at_kst = now_kst()
        delivery_at_kst = created_at_kst + relativedelta(months=self.months)

        # asyncpg는 timezone-aware datetime 객체를 timestamptz 컬럼에 그대로 저장할 수 있으므로
        # 별도의 ISO 문자열 변환 없이 UTC datetime 객체를 바로 전달한다.
        created_at_utc = created_at_kst.astimezone(timezone.utc)
        delivery_at_utc = delivery_at_kst.astimezone(timezone.utc)

        # 중요: DB 저장이 성공했는지 반드시 먼저 확인한 뒤에만 사용자에게 성공 메시지를 보여준다.
        try:
            capsule_id = await db.create_capsule(
                str(interaction.user.id),
                str(interaction.user),
                message_text,
                created_at_utc,
                delivery_at_utc,
            )
        except Exception:
            logger.exception("타임캡슐 생성 중 DB 오류 발생 (Supabase 연결 상태 확인 필요)")
            await interaction.response.send_message(
                "❌ 타임캡슐 저장 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.",
                ephemeral=True,
            )
            return

        logger.info(
            f"타임캡슐 생성 완료 (ID: {capsule_id}, 사용자: {interaction.user.id}, 기간: {self.months}개월)"
        )

        embed = discord.Embed(title="⏳ 타임캡슐이 만들어졌어요!", color=discord.Color.gold())
        embed.add_field(name="📅 작성일", value=created_at_kst.strftime("%Y-%m-%d %H:%M"), inline=True)
        embed.add_field(name="⏰ 도착 예정", value=delivery_at_kst.strftime("%Y-%m-%d %H:%M"), inline=True)
        embed.add_field(name="🆔 캡슐 번호", value=f"#{capsule_id}", inline=True)
        embed.add_field(name="💌 메시지", value=message_text, inline=False)
        embed.set_footer(text="타임캡슐이 도착하는 날 DM으로 알려드릴게요!")

        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        logger.exception(f"TimeCapsuleModal 처리 중 예외 발생: {error}")
        message = "❌ 처리 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요."
        try:
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except discord.HTTPException:
            pass


# ─────────────────────────────────────────────
# 기간 선택 버튼 / View
# ─────────────────────────────────────────────
class DurationButton(discord.ui.Button):
    def __init__(self, label: str, months: int):
        super().__init__(label=label, style=discord.ButtonStyle.primary)
        self.months = months

    async def callback(self, interaction: discord.Interaction):
        view: DurationSelectView = self.view

        # 버튼을 한 번만 사용할 수 있도록 즉시 비활성화 (이미 처리된 버튼 재사용 방지)
        for item in view.children:
            item.disabled = True

        modal = TimeCapsuleModal(months=self.months)
        await interaction.response.send_modal(modal)

        try:
            await interaction.message.edit(view=view)
        except discord.HTTPException:
            pass

        view.stop()


class DurationSelectView(discord.ui.View):
    def __init__(self, author_id: int):
        super().__init__(timeout=180)  # 3분 내 미선택 시 자동 만료
        self.author_id = author_id
        self.message: discord.Message | None = None

        for label, months in DURATION_OPTIONS.items():
            self.add_item(DurationButton(label, months))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "❌ 본인이 실행한 명령어에서만 선택할 수 있습니다.", ephemeral=True
            )
            return False
        return True

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


# ─────────────────────────────────────────────
# Flask 웹 서버 (Render Web Service 슬립 방지용)
# ─────────────────────────────────────────────
app = Flask(__name__)


@app.route("/")
def index():
    """UptimeRobot이 주기적으로 요청할 헬스 체크 엔드포인트"""
    return {"status": "running", "bot": "Time Capsule Bot"}, 200


@app.route("/ping")
def ping():
    """UptimeRobot 핑 테스트용 엔드포인트"""
    return "pong", 200


def run_flask():
    """Flask 서버를 별도 스레드에서 실행한다. (봇 실행을 차단하지 않도록)"""
    logger.info(f"Flask 서버 시작: 포트 {PORT}")
    app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)


# ─────────────────────────────────────────────
# 봇 클래스
# ─────────────────────────────────────────────
class TimeCapsuleBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        # 슬래시 커맨드만 사용하므로 MESSAGE CONTENT INTENT는 필요하지 않음
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        # Supabase(PostgreSQL) 초기화 (테이블이 없으면 자동 생성, 기존 데이터는 절대 건드리지 않음)
        # 연결에 실패해도 raise하지 않는다: Supabase 장애/설정 누락 때문에 봇 로그인 자체가
        # 막히지 않도록 하고, 대신 각 명령어 호출 시점에 개별적으로 오류를 안내한다.
        try:
            await db.init_db()
            logger.info("Supabase(PostgreSQL) 데이터베이스 초기화 완료")
        except Exception:
            logger.exception(
                "Supabase 연결/초기화에 실패했습니다. DATABASE_URL 환경변수와 Supabase 프로젝트 상태를 "
                "확인해주세요. 봇 로그인은 계속 진행되며, Supabase가 복구되면 다음 요청부터 자동으로 "
                "재연결을 시도합니다."
            )

        # Slash Command 동기화 (재시작마다 반복 호출되지 않도록 setup_hook에서 1회만 수행)
        try:
            synced = await self.tree.sync()
            logger.info(f"Slash Command {len(synced)}개 동기화 완료")
        except discord.HTTPException:
            logger.exception("Slash Command 동기화 실패")

        # 백그라운드 전송 작업 시작 (재시작 시 Supabase에 남아있던 미전송 타임캡슐도 자동으로 확인됨)
        if not check_capsules.is_running():
            check_capsules.start()

    async def close(self):
        # 봇 종료 시 Supabase 커넥션 풀을 안전하게 정리한다.
        try:
            await db.close_pool()
        except Exception:
            logger.exception("Supabase 커넥션 풀 종료 중 오류 발생")
        await super().close()


bot = TimeCapsuleBot()


@bot.event
async def on_ready():
    logger.info(f"봇 로그인 완료: {bot.user} (ID: {bot.user.id})")


# ─────────────────────────────────────────────
# 백그라운드 작업: 도착 시간이 된 타임캡슐 전송
# ─────────────────────────────────────────────
@tasks.loop(seconds=CHECK_INTERVAL_SECONDS)
async def check_capsules():
    try:
        now_utc = datetime.now(timezone.utc)
        pending = await db.get_pending_capsules(now_utc)
    except Exception:
        # Supabase가 일시적으로 응답하지 않아도 루프 자체는 죽지 않고 다음 주기에 재시도한다.
        logger.exception("미전송 타임캡슐 조회 중 DB 오류 발생 (Supabase 연결 상태 확인 필요)")
        return

    for capsule in pending:
        # 하나의 타임캡슐 전송 실패가 전체 루프를 중단시키지 않도록 개별적으로 예외 처리
        try:
            await deliver_capsule(capsule)
        except Exception:
            logger.exception(f"타임캡슐 #{capsule['capsule_id']} 처리 중 예상치 못한 오류 발생")


@check_capsules.before_loop
async def before_check_capsules():
    await bot.wait_until_ready()


async def deliver_capsule(capsule: dict) -> None:
    capsule_id = capsule["capsule_id"]
    user_id = capsule["user_id"]
    message = capsule["message"]
    created_at = capsule["created_at"]
    delivery_at = capsule["delivery_at"]

    # 사용자 조회
    try:
        user = await bot.fetch_user(int(user_id))
    except discord.NotFound:
        logger.warning(f"타임캡슐 #{capsule_id} 전송 실패: 사용자를 찾을 수 없음 (user_id={user_id})")
        await db.mark_delivery_failed(capsule_id)
        return
    except discord.HTTPException:
        logger.exception(f"타임캡슐 #{capsule_id} 사용자 조회 중 일시적 오류 발생 (다음 주기에 재시도)")
        return

    embed = discord.Embed(
        title="⏳ 타임캡슐이 도착했어요!",
        description=f"<@{user_id}>\n\n예전에 미래의 나에게 남겼던 메시지입니다.",
        color=discord.Color.gold(),
    )
    embed.add_field(name="💌", value=message, inline=False)
    embed.add_field(name="📅 작성일", value=to_kst_str(created_at), inline=True)
    embed.add_field(name="⏰ 도착일", value=to_kst_str(delivery_at), inline=True)

    # DM 전송
    try:
        await user.send(content=f"<@{user_id}>", embed=embed)
    except discord.Forbidden:
        # 사용자가 DM을 차단했거나 서버를 나간 경우 등 → 영구 실패로 표시하여 무한 재시도 방지
        logger.warning(f"타임캡슐 #{capsule_id} 전송 실패: DM이 차단되어 있음 (user_id={user_id})")
        await db.mark_delivery_failed(capsule_id)
        return
    except discord.HTTPException:
        # 일시적인 네트워크/API 오류 → 다음 주기에 다시 시도
        logger.exception(f"타임캡슐 #{capsule_id} DM 전송 중 일시적 오류 발생 (다음 주기에 재시도)")
        return

    # 전송 성공 → DB 갱신 (delivered=false 조건의 원자적 UPDATE이므로 중복 전송되지 않음)
    delivered_at_utc = datetime.now(timezone.utc)
    try:
        await db.mark_delivered(capsule_id, delivered_at_utc)
        logger.info(f"타임캡슐 #{capsule_id} 전송 성공 (user_id={user_id})")
    except Exception:
        logger.exception(f"타임캡슐 #{capsule_id} 전송 상태 업데이트 중 DB 오류 발생 (Supabase 연결 상태 확인 필요)")


# ─────────────────────────────────────────────
# Slash Command: /타임캡슐
# ─────────────────────────────────────────────
@bot.tree.command(name="타임캡슐", description="미래의 나에게 타임캡슐을 남깁니다.")
async def create_time_capsule(interaction: discord.Interaction):
    embed = discord.Embed(
        title="⏳ 타임캡슐 만들기",
        description=(
            "미래의 자신에게 메시지를 남겨보세요!\n\n"
            "아래에서 타임캡슐 기간을 선택해주세요."
        ),
        color=discord.Color.blurple(),
    )
    view = DurationSelectView(author_id=interaction.user.id)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
    view.message = await interaction.original_response()


# ─────────────────────────────────────────────
# Slash Command: /내타임캡슐
# ─────────────────────────────────────────────
@bot.tree.command(name="내타임캡슐", description="내가 만든 타임캡슐 목록을 확인합니다.")
async def list_my_capsules(interaction: discord.Interaction):
    try:
        capsules = await db.get_user_capsules(str(interaction.user.id))
    except Exception:
        logger.exception("타임캡슐 목록 조회 중 DB 오류 발생 (Supabase 연결 상태 확인 필요)")
        await interaction.response.send_message(
            "❌ 타임캡슐 목록을 불러오는 중 오류가 발생했습니다.", ephemeral=True
        )
        return

    if not capsules:
        await interaction.response.send_message(
            "아직 만든 타임캡슐이 없어요. `/타임캡슐` 명령어로 만들어보세요!", ephemeral=True
        )
        return

    embed = discord.Embed(title="📦 내 타임캡슐 목록", color=discord.Color.blurple())

    for capsule in capsules[:25]:  # Embed 필드는 최대 25개까지만 표시 가능
        if capsule["cancelled"]:
            status = "🚫 취소됨"
        elif capsule["delivered"]:
            status = f"✅ 전송 완료 ({to_kst_str(capsule['delivered_at'])})"
        elif capsule["delivery_failed"]:
            status = "⚠️ 전송 실패 (DM이 차단되어 있을 수 있어요)"
        else:
            status = f"⏳ 대기 중 · {remaining_str(capsule['delivery_at'])}"

        embed.add_field(
            name=f"#{capsule['capsule_id']} · 도착 예정 {to_kst_str(capsule['delivery_at'])}",
            value=status,
            inline=False,
        )

    if len(capsules) > 25:
        embed.set_footer(text=f"전체 {len(capsules)}개 중 25개만 표시됩니다. /타임캡슐조회 로 개별 확인해주세요.")

    await interaction.response.send_message(embed=embed, ephemeral=True)


# ─────────────────────────────────────────────
# Slash Command: /타임캡슐조회
# ─────────────────────────────────────────────
@bot.tree.command(name="타임캡슐조회", description="특정 타임캡슐의 상세 상태를 확인합니다.")
@app_commands.describe(캡슐번호="확인할 타임캡슐의 번호(ID)")
async def check_capsule_detail(interaction: discord.Interaction, 캡슐번호: int):
    try:
        capsule = await db.get_capsule_by_id(캡슐번호)
    except Exception:
        logger.exception("타임캡슐 조회 중 DB 오류 발생 (Supabase 연결 상태 확인 필요)")
        await interaction.response.send_message(
            "❌ 타임캡슐 조회 중 오류가 발생했습니다.", ephemeral=True
        )
        return

    if capsule is None or capsule["user_id"] != str(interaction.user.id):
        await interaction.response.send_message(
            "❌ 해당 번호의 타임캡슐을 찾을 수 없습니다. (본인 소유의 타임캡슐만 조회할 수 있어요)",
            ephemeral=True,
        )
        return

    if capsule["cancelled"]:
        status = "🚫 취소됨"
    elif capsule["delivered"]:
        status = f"✅ 전송 완료 ({to_kst_str(capsule['delivered_at'])})"
    elif capsule["delivery_failed"]:
        status = "⚠️ 전송 실패 (DM이 차단되어 있을 수 있어요)"
    else:
        status = f"⏳ 대기 중 · {remaining_str(capsule['delivery_at'])}"

    embed = discord.Embed(title=f"📦 타임캡슐 #{capsule['capsule_id']}", color=discord.Color.blurple())
    embed.add_field(name="상태", value=status, inline=False)
    embed.add_field(name="📅 작성일", value=to_kst_str(capsule["created_at"]), inline=True)
    embed.add_field(name="⏰ 도착 예정", value=to_kst_str(capsule["delivery_at"]), inline=True)
    embed.add_field(name="💌 메시지", value=capsule["message"], inline=False)

    await interaction.response.send_message(embed=embed, ephemeral=True)


# ─────────────────────────────────────────────
# Slash Command: /타임캡슐취소
# ─────────────────────────────────────────────
@bot.tree.command(name="타임캡슐취소", description="아직 도착하지 않은 타임캡슐을 취소합니다.")
@app_commands.describe(캡슐번호="취소할 타임캡슐의 번호(ID)")
async def cancel_time_capsule(interaction: discord.Interaction, 캡슐번호: int):
    try:
        capsule = await db.get_capsule_by_id(캡슐번호)
    except Exception:
        logger.exception("타임캡슐 취소 중 DB 조회 오류 발생 (Supabase 연결 상태 확인 필요)")
        await interaction.response.send_message(
            "❌ 타임캡슐 조회 중 오류가 발생했습니다.", ephemeral=True
        )
        return

    if capsule is None or capsule["user_id"] != str(interaction.user.id):
        await interaction.response.send_message(
            "❌ 해당 번호의 타임캡슐을 찾을 수 없습니다. (본인 소유의 타임캡슐만 취소할 수 있어요)",
            ephemeral=True,
        )
        return

    if capsule["delivered"]:
        await interaction.response.send_message(
            "❌ 이미 전송된 타임캡슐은 취소할 수 없습니다.", ephemeral=True
        )
        return

    if capsule["cancelled"]:
        await interaction.response.send_message("이미 취소된 타임캡슐입니다.", ephemeral=True)
        return

    try:
        success = await db.cancel_capsule(캡슐번호, str(interaction.user.id))
    except Exception:
        logger.exception("타임캡슐 취소 처리 중 DB 오류 발생 (Supabase 연결 상태 확인 필요)")
        await interaction.response.send_message(
            "❌ 취소 처리 중 오류가 발생했습니다.", ephemeral=True
        )
        return

    if success:
        logger.info(f"타임캡슐 #{캡슐번호} 취소됨 (user_id={interaction.user.id})")
        await interaction.response.send_message(f"✅ 타임캡슐 #{캡슐번호}을(를) 취소했습니다.", ephemeral=True)
    else:
        await interaction.response.send_message(
            "❌ 취소할 수 없는 상태입니다. (이미 전송되었거나 취소됨)", ephemeral=True
        )


# ─────────────────────────────────────────────
# 전역 Slash Command 오류 처리
# ─────────────────────────────────────────────
@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    logger.error(f"Slash Command 오류 발생: {error}")
    message = "❌ 명령어 처리 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요."
    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except discord.HTTPException:
        pass


# ─────────────────────────────────────────────
# 실행부
# ─────────────────────────────────────────────
if __name__ == "__main__":
    try:
        # Flask 서버를 데몬 스레드로 실행 (UptimeRobot이 주기적으로 요청하면 프로세스 계속 실행)
        flask_thread = threading.Thread(target=run_flask, daemon=True)
        flask_thread.start()
        logger.info("Flask 웹 서버 스레드 시작")

        # 메인 스레드에서는 Discord 봇 실행
        bot.run(TOKEN)
    except discord.LoginFailure:
        logger.error("Discord 로그인 실패: TOKEN 값이 올바른지 확인해주세요.")
    except Exception:
        logger.exception("봇 실행 중 예상치 못한 오류가 발생했습니다.")
