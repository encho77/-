# ⏳ 타임캡슐 Discord 봇

미래의 자신에게 메시지를 남기고, 지정한 기간이 지나면 DM으로 받아보는 Discord 봇입니다.

> 📌 **DB 저장소: Supabase PostgreSQL** (기존 로컬 SQLite에서 전환됨)
> Render에서 재배포/재시작이 발생해도 타임캡슐 데이터가 절대 사라지지 않습니다.

## 프로젝트 구조

```
time-capsule-bot/
├── main.py                  # 봇 실행 파일 (슬래시 커맨드, UI, 백그라운드 작업) — 기능 변경 없음
├── database.py               # Supabase(PostgreSQL) 접근 모듈 (asyncpg 기반, 완전 비동기)
├── supabase_schema.sql       # Supabase SQL Editor에서 실행할 테이블 생성 스크립트
├── migrate_to_supabase.py    # (선택) 기존 로컬 SQLite 데이터를 Supabase로 옮기는 1회성 스크립트
├── requirements.txt          # 필요한 패키지 목록
├── .gitignore
└── README.md
```

## 기능 (변경 없음)

| 명령어 | 설명 |
|---|---|
| `/타임캡슐` | 기간(1/3/6/9/12개월) 선택 후 메시지를 작성해 타임캡슐 생성 |
| `/내타임캡슐` | 내가 만든 타임캡슐 목록 확인 |
| `/타임캡슐조회 캡슐번호` | 특정 타임캡슐의 상세 상태 확인 |
| `/타임캡슐취소 캡슐번호` | 아직 도착하지 않은 타임캡슐 취소 (이미 전송된 것은 취소 불가) |

- 도착 시각이 되면 봇이 30초(기본값)마다 DB를 확인하여 자동으로 DM을 보냅니다.
- 기간 계산은 30일 곱셈이 아닌 **실제 달(month) 기준**(`dateutil.relativedelta`)으로 계산되며, 한국 시간(KST, UTC+9) 기준으로 처리됩니다.
- Render Web Service + UptimeRobot으로 무료 배포가 가능합니다 (Flask 헬스체크 엔드포인트 포함).

---

## 🔄 이번에 무엇이 바뀌었나 (SQLite → Supabase)

| 항목 | 이전 (SQLite) | 이후 (Supabase) |
|---|---|---|
| 저장소 | 로컬 파일 `database.db` | Supabase PostgreSQL (`time_capsules` 테이블) |
| 재배포 시 데이터 | **사라질 수 있음** | **항상 유지됨** |
| 연결 방식 | `sqlite3` + `threading.Lock`, `asyncio.to_thread`로 감쌈 | `asyncpg` 커넥션 풀, 완전 네이티브 비동기 |
| 시간 저장 | ISO 8601 문자열 | PostgreSQL `TIMESTAMPTZ` (Python datetime 객체 그대로 사용) |
| 환경변수 | `DATABASE_PATH` (선택) | `DATABASE_URL` (필수) |

**기능, 명령어, Embed, 버튼, Modal, 에러 처리, 로그는 전혀 변경되지 않았습니다.** DB 저장 방식만 교체되었습니다.

---

## 1. Supabase 프로젝트 설정

1. https://supabase.com 접속 → 회원가입/로그인 → **New Project** 생성
2. 프로젝트 생성 시 설정한 **Database Password**를 기억해두세요 (연결 문자열에 필요)
3. 왼쪽 메뉴 **SQL Editor** 이동 → `supabase_schema.sql` 파일 내용을 붙여넣고 **Run** 클릭
   - (참고: 봇이 처음 실행될 때도 동일한 `CREATE TABLE IF NOT EXISTS`를 자동 실행하므로, 이 단계는 미리 확인하고 싶을 때만 하면 됩니다)
4. 왼쪽 메뉴 **Project Settings → Database** 이동
5. **Connection String** 섹션에서 **⚠️ "Session pooler"** 탭을 선택하세요 (아래 설명 참고)
6. `URI` 형식의 연결 문자열을 복사하고, `[YOUR-PASSWORD]` 부분을 실제 비밀번호로 교체

### ⚠️ 왜 "Session pooler"를 사용해야 하나요?

Supabase의 **"Direct connection"**은 IPv6 주소로만 연결됩니다. Render를 포함한 대부분의 무료 호스팅 플랫폼은 **아웃바운드 IPv6를 지원하지 않아** Direct connection으로는 연결이 실패할 수 있습니다.

- ✅ **Session pooler** (포트 5432): IPv4 지원, 이 봇처럼 상시 연결을 유지하는 애플리케이션에 적합 → **권장**
- ⚠️ Transaction pooler (포트 6543): 서버리스 함수처럼 짧은 연결을 많이 맺는 경우용. 이 봇에는 불필요
- ❌ Direct connection: IPv6 전용, Render에서 연결 실패 가능성 높음

연결 문자열 예시 (Session pooler):
```
postgresql://postgres.xxxxxxxxxxxx:[YOUR-PASSWORD]@aws-0-ap-northeast-2.pooler.supabase.com:5432/postgres
```

---

## 2. Discord Developer Portal 설정

1. https://discord.com/developers/applications 접속 → **New Application** 생성
2. 왼쪽 메뉴 **Bot** 탭 이동 → **Reset Token**으로 봇 토큰 발급 (이 값을 `TOKEN` 환경변수로 사용)
3. **Privileged Gateway Intents**: 이 봇은 슬래시 커맨드와 DM 전송만 사용하므로 **MESSAGE CONTENT INTENT는 켤 필요 없습니다.**
4. 왼쪽 메뉴 **OAuth2 → URL Generator** 이동
   - **SCOPES**: `bot`, `applications.commands` 체크
   - **BOT PERMISSIONS**: `Send Messages`, `Embed Links`, `Use Slash Commands` 체크
5. 생성된 URL로 접속하여 봇을 원하는 서버에 초대
6. DM 전송을 위해서는 사용자가 **"서버 멤버로부터의 다이렉트 메시지 허용"** 설정을 켜두어야 합니다.

---

## 3. 로컬에서 테스트하기

```bash
# 1) 패키지 설치
pip install -r requirements.txt

# 2) 환경변수 설정
export TOKEN="여기에_발급받은_봇_토큰"
export DATABASE_URL="postgresql://postgres.xxxx:[YOUR-PASSWORD]@aws-0-xxxx.pooler.supabase.com:5432/postgres"

# 3) 실행
python main.py
```

정상 실행 시 로그 예시:
```
Supabase(PostgreSQL) 커넥션 풀 생성 완료
Supabase(PostgreSQL) 데이터베이스 초기화 완료
Slash Command 4개 동기화 완료
Flask 웹 서버 스레드 시작
봇 로그인 완료: ...
```

---

## 4. Render 배포 (Web Service + UptimeRobot)

### 4-1. Render Web Service 설정

1. https://render.com → **New → Web Service** → GitHub 저장소 연결
2. **Build Command**: `pip install -r requirements.txt`
3. **Start Command**: `python main.py` (기존과 동일하게 유지됨)

### 4-2. Environment Variables (Render 대시보드 → Environment)

| Key | Value | 설명 |
|---|---|---|
| `TOKEN` | 발급받은 봇 토큰 | 필수 (기존과 동일) |
| `DATABASE_URL` | Supabase Session pooler 연결 문자열 | **필수** (신규) |
| `MAX_CAPSULES_PER_USER` | 예: `10` | 선택 (기존과 동일) |
| `CHECK_INTERVAL_SECONDS` | 예: `30` | 선택 (기존과 동일) |

> ℹ️ 기존에 사용하던 `DATABASE_PATH` 환경변수는 더 이상 사용되지 않습니다. (SQLite 파일 경로였음 — Supabase로 전환되며 불필요해짐) 설정되어 있어도 무시되니 지워도, 남겨둬도 무방합니다.

### 4-3. UptimeRobot 설정 (Render 슬립 방지 — 기존과 동일)

1. https://uptimerobot.com → **Add New Monitor**
2. **Monitor Type**: HTTP(s), **URL**: `https://your-render-url.onrender.com/`, **Interval**: 5분
3. 저장

> ⚠️ **중요**: UptimeRobot은 Render 프로세스가 잠들지 않게만 해줄 뿐입니다. 타임캡슐 데이터의 영구 저장은 전적으로 Supabase가 담당하므로, UptimeRobot 설정 여부와 무관하게 데이터는 안전합니다.

---

## 5. 기존 SQLite 데이터 마이그레이션 (선택)

로컬 `database.db`에 이미 만들어둔 타임캡슐이 있다면, 아래 스크립트로 Supabase로 옮길 수 있습니다.
**자동으로 실행되지 않으며, 실행 시 확인 메시지가 뜨고, 기존 SQLite 파일은 삭제하지 않습니다.**

```bash
export DATABASE_URL="postgresql://postgres.xxxx:[YOUR-PASSWORD]@aws-0-xxxx.pooler.supabase.com:5432/postgres"
python migrate_to_supabase.py
# 다른 경로의 SQLite 파일을 쓰려면: python migrate_to_supabase.py /path/to/database.db
```

실행하면 이전할 타임캡슐 개수를 보여주고 `y` 입력 시에만 진행합니다.

---

## 6. 테스트 방법 (요청하신 3가지 시나리오)

### 테스트 1: 재배포 후 데이터 유지 확인
1. `/타임캡슐` → 기간 선택 → 메시지 작성 → 생성 완료 확인
2. Render 대시보드에서 **Manual Deploy → Deploy latest commit** (재배포)
3. 재배포 완료 후 `/내타임캡슐` 실행 → 방금 만든 캡슐이 그대로 남아있는지 확인

### 테스트 2: 봇이 꺼져있는 동안 시간이 지난 경우
1. Supabase 대시보드 → **Table Editor → time_capsules** → 테스트용 캡슐의 `delivery_at` 값을 현재 시각보다 과거로 직접 수정
2. Render에서 봇을 잠시 중지했다가 다시 시작 (또는 로컬에서 봇 재시작)
3. `CHECK_INTERVAL_SECONDS`(기본 30초) 이내에 DM이 오는지 확인

### 테스트 3: 중복 발송 방지 확인
1. 테스트 2에서 DM을 받은 캡슐의 `delivered` 값이 Supabase에서 `true`로 바뀌었는지 확인
2. 봇을 다시 재시작
3. 같은 캡슐로 DM이 다시 오지 않는지 확인 (`delivered = false` 조건의 원자적 UPDATE로 보장됨)

### Supabase에서 데이터 직접 확인하기
Supabase 대시보드 → **Table Editor → time_capsules**에서 모든 타임캡슐의 실시간 상태(`delivered`, `cancelled`, `delivery_failed` 등)를 직접 볼 수 있습니다.

---

## 7. 자주 발생하는 오류와 해결 방법

| 증상 | 원인 / 해결 방법 |
|---|---|
| `환경변수 DATABASE_URL이 설정되지 않았습니다` (로그 경고) | Render Environment에 `DATABASE_URL`을 추가하지 않음. 봇은 계속 실행되지만 DB 관련 명령어는 오류 메시지를 반환함 |
| 봇 실행은 되는데 `/타임캡슐` 생성 시 "데이터베이스 오류" | `DATABASE_URL` 값이 잘못됐거나 비밀번호가 틀림. Supabase 대시보드에서 연결 문자열을 다시 복사 |
| 연결이 계속 타임아웃됨 | Direct connection(IPv6)을 사용 중일 가능성. **Session pooler** 연결 문자열(포트 5432)로 교체 |
| `prepared statement "..." does not exist` 오류 | Transaction pooler(포트 6543) 사용 시 발생 가능. 이 코드는 `statement_cache_size=0`으로 방어 처리되어 있지만, 가급적 Session pooler 사용 권장 |
| 같은 타임캡슐이 여러 번 전송될까 걱정됨 | `UPDATE ... WHERE delivered = FALSE` 조건의 원자적 쿼리를 사용하므로 이미 전송된 캡슐은 다시 전송되지 않음 |
| Render 재배포 후에도 데이터가 사라짐 | Supabase가 아니라 여전히 로컬 SQLite를 쓰고 있는 것은 아닌지 `database.py`가 최신 버전인지 확인. `DATABASE_URL`이 정확히 설정되었는지도 확인 |
| DM이 오지 않음 | 사용자가 DM을 차단했거나 "서버 멤버의 다이렉트 메시지 허용"을 꺼둔 경우. `/내타임캡슐`에서 "⚠️ 전송 실패"로 표시됨 |
| Render에서 15분마다 봇이 꺼짐 | UptimeRobot Monitor URL과 Interval 설정 확인 |
