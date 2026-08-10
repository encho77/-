# ⏳ 타임캡슐 Discord 봇

미래의 자신에게 메시지를 남기고, 지정한 기간이 지나면 DM으로 받아보는 Discord 봇입니다.

## 프로젝트 구조

```
time-capsule-bot/
├── main.py            # 봇 실행 파일 (슬래시 커맨드, UI, 백그라운드 작업)
├── database.py         # SQLite 데이터베이스 접근 모듈
├── requirements.txt    # 필요한 패키지 목록
├── .gitignore
└── README.md
```

## 기능

| 명령어 | 설명 |
|---|---|
| `/타임캡슐` | 기간(1/3/6/9/12개월) 선택 후 메시지를 작성해 타임캡슐 생성 |
| `/내타임캡슐` | 내가 만든 타임캡슐 목록 확인 |
| `/타임캡슐조회 캡슐번호` | 특정 타임캡슐의 상세 상태 확인 |
| `/타임캡슐취소 캡슐번호` | 아직 도착하지 않은 타임캡슐 취소 (이미 전송된 것은 취소 불가) |

- 도착 시각이 되면 봇이 30초(기본값)마다 DB를 확인하여 자동으로 DM을 보냅니다.
- 봇이 재시작되거나 Render에서 재배포되어도 SQLite에 저장된 타임캡슐은 그대로 유지되며, 이미 도착 시간이 지난 미전송 타임캡슐도 다시 켜지는 즉시 확인 후 전송됩니다.
- 기간 계산은 30일 곱셈이 아닌 **실제 달(month) 기준**(`dateutil.relativedelta`)으로 계산되며, 한국 시간(KST, UTC+9) 기준으로 처리됩니다.

---

## 1. Discord Developer Portal 설정

1. https://discord.com/developers/applications 접속 → **New Application** 생성
2. 왼쪽 메뉴 **Bot** 탭 이동 → **Reset Token**으로 봇 토큰 발급 (이 값을 `TOKEN` 환경변수로 사용)
3. **Privileged Gateway Intents**: 이 봇은 슬래시 커맨드와 DM 전송만 사용하므로 **MESSAGE CONTENT INTENT는 켤 필요 없습니다.** (기본값 그대로 두면 됩니다)
4. 왼쪽 메뉴 **OAuth2 → URL Generator** 이동
   - **SCOPES**: `bot`, `applications.commands` 체크
   - **BOT PERMISSIONS**: `Send Messages`, `Embed Links`, `Use Slash Commands` 체크 (서버 채널에는 메시지를 보내지 않지만, 명령어 등록/실행을 위해 필요)
5. 생성된 URL로 접속하여 봇을 원하는 서버에 초대
6. DM 전송을 위해서는 사용자가 **"서버 멤버로부터의 다이렉트 메시지 허용"** 설정을 켜두어야 합니다. (사용자가 차단한 경우 봇이 자동으로 감지하여 오류 없이 넘어갑니다)

---

## 2. 로컬에서 테스트하기

```bash
# 1) 패키지 설치
pip install -r requirements.txt

# 2) 환경변수 설정 (Windows: set, macOS/Linux: export)
# macOS/Linux
export TOKEN="여기에_발급받은_봇_토큰"

# Windows (PowerShell)
$env:TOKEN="여기에_발급받은_봇_토큰"

# 3) 실행
python main.py
```

`.env` 파일을 쓰고 싶다면 `python-dotenv`를 별도로 설치(`pip install python-dotenv`)한 뒤,
`main.py` 최상단에 아래 두 줄을 추가하면 됩니다. (기본 프로젝트에는 의존성을 최소화하기 위해 포함하지 않았습니다)

```python
from dotenv import load_dotenv
load_dotenv()
```

> ⚠️ `.env` 파일에 토큰을 넣었다면 **절대 GitHub에 올리지 마세요.** `.gitignore`에 이미 `.env`가 포함되어 있습니다.

---

## 3. GitHub 업로드

```bash
git init
git add .
git commit -m "타임캡슐 봇 초기 버전"
git branch -M main
git remote add origin <본인의 GitHub 저장소 URL>
git push -u origin main
```

`.gitignore`에 `.env`, `database.db`가 포함되어 있으므로 토큰과 로컬 DB 파일은 업로드되지 않습니다.

---

## 4. Render 배포

1. https://render.com 접속 → **New → Web Service** (또는 **Background Worker**, 아래 참고)
2. GitHub 저장소 연결
3. **Build Command**: `pip install -r requirements.txt`
4. **Start Command**: `python main.py`

### Environment Variables (Render 대시보드 → Environment)

| Key | Value | 설명 |
|---|---|---|
| `TOKEN` | 발급받은 봇 토큰 | 필수 |
| `DATABASE_PATH` | 예: `/data/database.db` | 선택 (아래 5번 항목 참고) |
| `MAX_CAPSULES_PER_USER` | 예: `10` | 선택, 사용자당 최대 보관 개수 (기본값 10) |
| `CHECK_INTERVAL_SECONDS` | 예: `30` | 선택, 전송 확인 주기(초) (기본값 30) |

> 이 봇은 웹 서버가 아니라 상시 실행되는 프로세스이므로, Render의 **Background Worker** 유형으로 배포하는 것을 권장합니다. Web Service로 배포할 경우 Render가 헬스체크를 위한 포트 바인딩을 요구할 수 있으니, 만약 오류가 발생하면 Background Worker로 전환해주세요.

---

## 5. ⚠️ Render 파일 저장(영속성) 관련 중요 안내

Render의 기본 파일 시스템은 **재배포하거나 인스턴스가 교체될 때 초기화될 수 있습니다.**
즉, `database.db`를 기본 경로(프로젝트 루트)에 그대로 두면, 코드를 다시 배포(Deploy)할 때마다 데이터가 사라질 수 있습니다.

- 단순 **재시작(Restart)**이나 **크래시 후 자동 재기동**의 경우에는 대부분 동일 디스크가 유지되어 데이터가 보존됩니다.
- 하지만 **Git push로 인한 재배포, 플랜 변경 등**의 경우에는 새 인스턴스로 교체되며 파일이 초기화될 수 있습니다.

**해결 방법**: Render의 **Persistent Disk**(유료 애드온, Starter 플랜부터 지원)를 서비스에 연결하고, 환경변수 `DATABASE_PATH`를 디스크 마운트 경로로 지정하세요.

```
DATABASE_PATH=/data/database.db
```

이 봇 코드는 `DATABASE_PATH` 환경변수를 지원하도록 이미 구현되어 있으며, 값이 없으면 프로젝트 루트의 `database.db`를 사용합니다. Persistent Disk 없이 무료로 운영할 경우, 재배포 시 타임캡슐 데이터가 사라질 수 있다는 점을 감안해주세요.

---

## 6. 기능 테스트 방법

1. 디스코드 서버에서 `/타임캡슐` 입력
2. 원하는 기간 버튼 클릭 (예: `1개월`)
3. 팝업창(Modal)에 "안녕 미래의 나! 지금도 게임 열심히 하고 있니?" 입력 후 제출
4. "⏳ 타임캡슐이 만들어졌어요!" 임베드와 캡슐 번호 확인
5. `/내타임캡슐` 또는 `/타임캡슐조회 캡슐번호`로 상태(대기 중 / 남은 시간) 확인
6. 테스트를 빠르게 하고 싶다면, 로컬에서 `main.py`의 `DURATION_OPTIONS`를 잠시 분 단위로 바꾸거나, `database.py`를 통해 직접 `delivery_at` 값을 과거 시각으로 수정한 뒤 봇을 실행하면 다음 확인 주기(`CHECK_INTERVAL_SECONDS`, 기본 30초)에 바로 DM이 전송되는 것을 확인할 수 있습니다.
7. `/타임캡슐취소 캡슐번호`로 아직 도착하지 않은 캡슐을 취소해보고, 전송 완료된 캡슐은 취소가 거부되는지 확인

## 7. 재시작 후 데이터 유지 확인 방법

1. `/타임캡슐`로 하나 생성 (예: 1개월 후 도착)
2. `Ctrl+C`로 봇을 종료했다가 `python main.py`로 다시 실행
3. `/내타임캡슐`로 방금 만든 캡슐이 여전히 남아있는지 확인 → 남아있다면 SQLite 저장이 정상 동작하는 것입니다.
4. (장시간 정지 시나리오 테스트) `database.db`에서 특정 캡슐의 `delivery_at` 값을 현재 시각보다 과거로 직접 수정한 뒤 봇을 재시작 → `CHECK_INTERVAL_SECONDS` 이내에 바로 DM이 오는지 확인

---

## 8. 자주 발생하는 오류와 해결 방법

| 증상 | 원인 / 해결 방법 |
|---|---|
| `RuntimeError: 환경변수 TOKEN이 설정되지 않았습니다` | `TOKEN` 환경변수를 설정하지 않음. 로컬은 `export TOKEN=...`, Render는 Environment 탭에서 설정 |
| `discord.LoginFailure` | 토큰 값이 잘못됨. Developer Portal에서 토큰을 다시 발급받아 확인 |
| 슬래시 커맨드가 디스코드에 안 보임 | 글로벌 동기화는 반영까지 최대 1시간 정도 걸릴 수 있음. 봇을 초대할 때 `applications.commands` 스코프를 빠뜨리지 않았는지 확인 |
| DM이 오지 않음 | 사용자가 DM을 차단했거나 "서버 멤버의 다이렉트 메시지 허용"을 꺼둔 경우 (`/내타임캡슐`에서 "⚠️ 전송 실패"로 표시됨). 봇 로그에서 `discord.Forbidden` 관련 로그 확인 |
| 재배포 후 타임캡슐이 사라짐 | Render의 파일 시스템 초기화 문제. 위 5번 항목 참고, Persistent Disk + `DATABASE_PATH` 설정 필요 |
| 버튼을 눌러도 반응 없음 | `/타임캡슐` 메시지가 3분(180초)이 지나 만료됨. 명령어를 다시 실행 |
| 같은 타임캡슐이 여러 번 전송될까 걱정됨 | DB 업데이트 시 `WHERE delivered = 0` 조건을 사용하므로 이미 전송된 캡슐은 다시 전송되지 않습니다 |
