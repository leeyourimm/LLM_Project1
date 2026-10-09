# 서버에 배포하기

리눅스 서버 한 대에 Docker로 서비스를 띄우고 https로 공개하는 방법입니다. 처음 해 보는 분도 위에서부터 한 줄씩 따라 하면 되도록 썼습니다. 명령은 한 줄에 하나씩, 서버 터미널에 그대로 붙여 넣으세요.

## 구성

| 컨테이너 | 하는 일 |
| --- | --- |
| caddy | https 인증서 자동 발급, 바깥 요청을 받아 나눠 줌 (80·443 포트만 열림) |
| frontend | 웹 화면 (Next.js) |
| api | FastAPI 백엔드 (`/api/*`) |
| worker, beat | 공시 수집·처리·알림 백그라운드 작업 (Celery) |
| postgres, redis, qdrant, opensearch | DB, 작업 대기열·캐시, 벡터 검색, 키워드 검색 |
| prometheus, grafana | 운영 지표 (선택, `monitoring` 프로필) |

답변 생성용 Ollama는 기본으로 **서버에 직접 설치**한 것을 씁니다. 컨테이너로 띄우는 방법도 아래에 있습니다.

관련 파일은 모두 `infra/prod/`에 있습니다.

- `docker-compose.yml`: 위 컨테이너 구성
- `Caddyfile`: 주소별 연결, 보안 헤더
- `.env.example`: 설정 목록 (복사해서 `.env`로 씀)
- `backup.sh`, `restore.sh`: 백업과 되돌리기

## 준비물

- 리눅스 서버 (Ubuntu 22.04 또는 24.04 기준으로 설명). 권장 사양: CPU 4코어 이상, 메모리 16GB 이상, 디스크 100GB 이상. 임베딩 모델(bge-m3)을 api와 작업자가 각각 메모리에 올리고, 리랭커를 켜면 더 씁니다. 메모리가 부족하면 `.env`에서 `RERANK_MODEL`을 비우세요.
- 답변 모델(qwen3:8b)은 GPU가 있으면 훨씬 빠릅니다. CPU만 있으면 답변 하나에 수십 초 이상 걸릴 수 있습니다.
- 도메인 하나 (예: `dart.example.com`). DNS 관리 화면에서 A 레코드로 서버의 공인 IP를 가리키게 해 두세요.
- OpenDART 인증키 (https://opendart.fss.or.kr)

## 1. 서버 기본 설정

Docker를 설치합니다 (공식 설치 스크립트).

```bash
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
sudo usermod -aG docker $USER
```

마지막 줄을 적용하려면 로그아웃했다가 다시 접속합니다. 그다음 아래 명령이 오류 없이 버전을 보여 주면 됩니다.

```bash
docker compose version
```

방화벽은 SSH, 웹(80·443)만 엽니다. 마지막 줄은 컨테이너가 서버의 Ollama(11434 포트)에 접속하도록 Docker 내부망만 허용하는 것입니다.

```bash
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw allow 443/udp
sudo ufw allow from 172.16.0.0/12 to any port 11434 proto tcp
sudo ufw enable
```

## 2. Ollama 설치 (서버에 직접)

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

컨테이너에서 접속할 수 있도록 Ollama가 모든 주소에서 듣게 바꿉니다. 바깥에서는 위 방화벽이 11434 포트를 막습니다.

```bash
sudo mkdir -p /etc/systemd/system/ollama.service.d
printf '[Service]\nEnvironment="OLLAMA_HOST=0.0.0.0:11434"\n' | sudo tee /etc/systemd/system/ollama.service.d/override.conf
sudo systemctl daemon-reload
sudo systemctl restart ollama
ollama pull qwen3:8b
```

(선택) 기본 모델이 실패하거나 첫 글자가 늦을 때 대신 답할 작은 모델도 받아 두려면 아래를 실행하고, 3단계에서 `.env`의 `LLM_FALLBACK_MODEL=qwen3:1.7b`를 채웁니다. 동작과 지표는 README의 "답변 모델 재시도와 대체 모델"에 있습니다.

```bash
ollama pull qwen3:1.7b
```

컨테이너로 띄우고 싶다면 이 단계를 건너뛰고 "Ollama를 컨테이너로" 절을 보세요.

## 3. 코드 받기와 설정

```bash
sudo mkdir -p /opt/dartrag
sudo chown $USER /opt/dartrag
git clone https://github.com/leeyourimm/LLM_Project1.git /opt/dartrag
cd /opt/dartrag/infra/prod
cp .env.example .env
```

비밀값을 만듭니다. 아래 명령을 세 번 실행해 나온 값을 각각 `SECRET_KEY`, `POSTGRES_PASSWORD`, `GRAFANA_ADMIN_PASSWORD`에 씁니다.

```bash
openssl rand -hex 32
```

`.env`를 엽니다.

```bash
nano .env
```

꼭 바꿀 것:

- `DOMAIN`: 내 도메인 (예: `dart.example.com`)
- `PUBLIC_URL`: `https://` + 내 도메인
- `SECRET_KEY`, `POSTGRES_PASSWORD`, `GRAFANA_ADMIN_PASSWORD`: 위에서 만든 값
- `DART_API_KEY`: OpenDART 인증키
- `ALLOW_SIGNUP`: 아무나 가입하지 못하게 하려면 `false`

`AUTH_REQUIRED=true`, `COOKIE_SECURE=true`, `ENVIRONMENT=production`은 이미 들어 있습니다. 나머지 항목은 설명을 읽고 필요할 때 채우면 됩니다. 저장은 `Ctrl+O`, `Enter`, 나가기는 `Ctrl+X`입니다.

`.env`는 비밀값이 들어 있으니 git에 올리지 않습니다 (`.gitignore`에 들어 있음). 다른 사람이 읽지 못하게 권한도 줄입니다.

```bash
chmod 600 .env
```

## 4. 처음 실행

이후 명령은 모두 `/opt/dartrag/infra/prod` 폴더에서 실행합니다.

```bash
cd /opt/dartrag/infra/prod
docker compose up -d --build
```

처음에는 이미지를 만들고 내려받느라 10~20분 걸립니다. 상태를 확인합니다.

```bash
docker compose ps
```

모든 줄의 STATUS가 `Up ... (healthy)`가 되면 준비된 것입니다 (beat, caddy는 healthy 표시가 없습니다). api는 처음 시작할 때 DB 표를 만들고, 작업자는 첫 작업 때 임베딩 모델(약 2GB)을 내려받아 몇 분 더 걸립니다.

브라우저에서 `https://내 도메인`을 엽니다. 인증서는 처음 접속할 때 자동으로 발급됩니다. 1분이 지나도 열리지 않으면 아래 "문제 해결"을 보세요.

로그인 계정을 만듭니다 (비밀번호는 화면에 보이지 않게 입력받습니다).

```bash
docker compose exec api dartrag user add me@example.com
```

준비 상태를 점검하고, 기본 15개 회사의 데이터를 처음 한 번 채웁니다. 몇 시간 걸릴 수 있으니 SSH 연결이 끊겨도 계속되도록 `-d`(백그라운드)로 실행합니다.

```bash
docker compose exec api dartrag doctor
docker compose exec -d worker dartrag run --eval-limit 0
```

진행 상황은 작업자 로그로 봅니다 (다음 절). 이후 새 공시는 작업자가 10분마다 자동으로 받습니다. 전체 상장사의 과거 데이터를 채우려면 `.env`에서 `BACKFILL_ENABLED=true`로 바꾸고 "설정 바꾸기"대로 다시 띄웁니다.

텔레그램 알림 봇을 쓴다면 `.env`에 `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET`을 넣고 아래로 웹훅을 등록합니다.

```bash
docker compose exec api dartrag telegram webhook
```

### 웹 푸시 알림 (선택, 무료)

공시 알림을 브라우저 알림으로도 받게 하려면 서버 키(VAPID)를 한 번 만듭니다. 브라우저 회사의 푸시 서비스(Chrome·Edge·삼성 인터넷은 Google, Firefox는 Mozilla, Safari는 Apple)를 거쳐 가며 가입이나 비용이 들지 않습니다. 아래 첫 명령이 키를 만들어 `.env` 끝에 붙이고, 비밀키는 화면에 보이지 않습니다. 이어서 다시 띄우고 점검합니다.

```bash
docker compose run --rm --no-deps -T api dartrag push keys --print >> .env
docker compose up -d
docker compose exec api dartrag doctor
```

`doctor`에 웹 푸시 경고가 없으면 사용자는 관심 종목 화면의 "알림 받을 곳"에서 "이 브라우저에서 받기"를 누르면 됩니다.

- 키가 이미 있으면 명령이 아무것도 붙이지 않고 멈춥니다. 키를 바꾸면 모든 사용자의 구독이 끊겨 다시 받아야 하므로, 비밀키가 새었을 때만 `.env`에서 `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY` 두 줄을 지우고 위 명령을 다시 실행합니다.
- `VAPID_SUBJECT`는 푸시 서비스가 문제가 있을 때 연락할 주소입니다. 비우면 `PUBLIC_URL`을 쓰고, 메일로 받으려면 `mailto:admin@example.com`처럼 넣습니다.
- 웹 푸시는 https 주소에서만 됩니다. 아이폰·아이패드(iOS 16.4 이상)는 Safari 공유 메뉴의 "홈 화면에 추가"로 연 화면에서만 받을 수 있습니다.
- 개발용으로 내 컴퓨터에서 쓸 때는 저장소 폴더에서 `dartrag push keys`를 실행하면 `.env`에 바로 넣습니다 (`http://localhost`도 됩니다).

### 2단계 인증

설정할 것은 없습니다. 사용자가 계정 화면에서 인증 앱(Google Authenticator 등)을 등록해 켭니다. 알아 둘 점:

- 메일로 비밀번호를 재설정해도 2단계 인증은 꺼지지 않습니다. 재설정한 기기에서도 인증 앱 코드나 복구 코드를 넣어야 로그인됩니다.
- 인증 앱 키는 `SECRET_KEY`로 암호화해 둡니다. `SECRET_KEY`를 바꾸면 2단계 인증을 켠 사용자는 인증 앱 코드로 로그인할 수 없고, 복구 코드로 들어와 끄고 다시 켜야 합니다.
- 휴대폰과 복구 코드를 모두 잃은 사용자는 본인인지 확인한 뒤 아래로 2단계 인증을 끕니다. 그 계정의 로그인도 모두 끊깁니다.

```bash
docker compose exec api dartrag user 2fa-off me@example.com
```

## 5. 상태 확인

서버 안에서 한 번에 보기:

```bash
docker compose ps
```

바깥에서 API가 응답하는지 (`{"ok":true}`가 나오면 정상):

```bash
curl https://dart.example.com/api/health
```

`/metrics`는 바깥에서 막혀 있어야 합니다 (404가 정상):

```bash
curl -o /dev/null -w "%{http_code}\n" https://dart.example.com/metrics
```

백그라운드 작업 상태:

```bash
docker compose exec api dartrag jobs status
```

## 6. 로그 보기

```bash
docker compose logs --tail 100 api
docker compose logs -f worker
docker compose logs --since 1h caddy
```

`-f`로 띄운 로그는 `Ctrl+C`로 빠져나옵니다. 로그 파일은 컨테이너마다 20MB씩 5개까지만 남기고 자동으로 지워집니다.

## 7. 업데이트

새 코드를 받아 이미지를 다시 만들고 바뀐 컨테이너만 다시 띄웁니다. DB 변경(마이그레이션)은 api가 시작하면서 자동으로 적용합니다.

```bash
cd /opt/dartrag
git pull
cd infra/prod
docker compose up -d --build
docker image prune -f
```

업데이트 전에 백업을 한 번 해 두면 안전합니다 (다음 절).

### 의존성 버전 (잠금 파일)

백엔드 이미지와 CI는 `pyproject.toml` 이 아니라 `requirements/` 의 잠금 파일로 설치합니다 (버전과 해시 고정, Linux x86_64·Python 3.12 기준).

- `requirements/runtime.txt`: 배포 이미지용 (`ops`, `embed`). PyTorch·CUDA 패키지는 뺍니다.
- `requirements/torch.txt`: PyTorch 버전. 이미지는 CPU 전용 색인에서 이 버전을 받습니다 (해시는 고정하지 않음).
- `requirements/dev.txt`: 테스트·CI용 (`dev`). 내 컴퓨터에서는 지금처럼 `pip install -e ".[dev]"` 를 써도 됩니다.

`pyproject.toml` 의 의존성을 바꿨거나 버전을 올릴 때는 [uv](https://docs.astral.sh/uv/)를 설치한 뒤 다시 만들고 함께 커밋합니다. CI의 `audit` 작업이 잠금 파일(pip-audit)과 웹 화면 패키지(`npm audit`)의 알려진 취약점을 검사합니다. Dependabot이 매주 업데이트 PR을 엽니다.

```bash
make lock                 # pyproject.toml 에 맞춰 다시 만든다 (이미 고정된 버전은 되도록 유지)
make lock UPGRADE=-U      # 모두 최신으로
make lock UPGRADE="-P fastapi"   # 한 패키지만 올리기
```

## 8. 설정 바꾸기

`.env`를 고친 뒤 다시 띄우면 바뀐 컨테이너만 새로 만들어집니다.

```bash
nano .env
docker compose up -d
```

## 9. 백업과 되돌리기

`backup.sh`는 Postgres 전체(`pg_dump` custom 형식)와 Qdrant 컬렉션 스냅샷을 `infra/prod/backups/<날짜-시각>/`에 저장합니다.

- **30일이 지난 백업은 자동으로 지웁니다.** 개인정보처리방침에 "백업 사본은 최대 30일 뒤 사라진다"고 적혀 있으니 이 기간을 늘리지 마세요.
- `.env`의 `BACKUP_DIR`로 저장 폴더를 바꿀 수 있습니다.
- `.env`에 `BACKUP_S3_BUCKET=버킷이름/dartrag`을 넣으면 S3에도 올리고, S3에서도 30일이 지난 것을 지웁니다. 서버에 aws cli가 설치되어 있고 `aws configure`로 자격 증명이 들어 있어야 합니다. 만일을 위해 버킷에도 30일 뒤 삭제하는 수명 주기 규칙을 걸어 두세요.
- OpenSearch 색인과 원문 파일은 백업하지 않습니다. 원문은 OpenDART에서 다시 받을 수 있고, 색인은 DB에서 다시 만들 수 있습니다.

지금 바로 백업:

```bash
/opt/dartrag/infra/prod/backup.sh
```

매일 새벽 4시에 자동으로 백업하려면 crontab을 엽니다.

```bash
crontab -e
```

맨 아래에 다음 한 줄을 넣고 저장합니다. 실행 기록은 홈 폴더의 `dartrag-backup.log`에 남습니다.

```
0 4 * * * /opt/dartrag/infra/prod/backup.sh >> $HOME/dartrag-backup.log 2>&1
```

백업 목록 보기:

```bash
ls /opt/dartrag/infra/prod/backups
```

되돌리기 (폴더 이름은 위 목록에서 고릅니다). `yes`를 입력해야 진행되고, 지금 데이터는 백업 시점으로 덮어써집니다.

```bash
/opt/dartrag/infra/prod/restore.sh /opt/dartrag/infra/prod/backups/20260101-040000
```

S3에만 있는 백업은 먼저 내려받습니다.

```bash
aws s3 cp --recursive s3://버킷이름/dartrag/20260101-040000/ /opt/dartrag/infra/prod/backups/20260101-040000/
```

새 서버로 옮겨서 되돌린 경우에는 OpenSearch 색인이 비어 있으므로 색인을 처음부터 다시 만듭니다 (공시 양에 따라 오래 걸립니다).

```bash
docker compose exec postgres psql -U dartrag -d dartrag -c "UPDATE filings SET indexed_at = NULL"
docker compose exec -d worker dartrag index
```

## 10. 모니터링 (Prometheus + Grafana)

메모리를 아끼려고 기본으로는 띄우지 않습니다. Grafana는 `https://내 도메인/grafana/`에서 열리고, 앞에 비밀번호(basic auth)가 한 번 더 걸립니다.

먼저 앞단 비밀번호의 해시를 만듭니다. 명령을 실행하면 비밀번호를 두 번 묻고 `$2a$14$...` 형태의 해시를 보여 줍니다.

```bash
docker run --rm -it caddy:2.10.2 caddy hash-password
```

`.env`에 해시를 **작은따옴표로 감싸서** 넣습니다 (해시 속 `$` 때문).

```
GRAFANA_BASIC_AUTH_USER=admin
GRAFANA_BASIC_AUTH_HASH='$2a$14$여기에해시'
```

모니터링과 함께 띄웁니다.

```bash
docker compose --profile monitoring up -d
```

브라우저에서 `https://내 도메인/grafana/`를 열고, 먼저 위 앞단 비밀번호, 다음에 Grafana 로그인(`admin` / `.env`의 `GRAFANA_ADMIN_PASSWORD`)을 입력합니다. "DART RAG" 대시보드가 기본 화면입니다.

- Prometheus는 바깥에 열리지 않고, Docker 내부망에서 `api:8000/metrics`를 15초마다 읽어 30일 동안 보관합니다.
- 경보 규칙은 `infra/monitoring/prometheus/alerts.yml`에 있습니다.
- Prometheus가 지표를 잘 읽는지는 아래로 확인합니다. `"health":"up"`이 보이면 정상입니다.

```bash
docker compose --profile monitoring exec prometheus wget -qO- http://127.0.0.1:9090/api/v1/targets
```

모니터링을 끌 때:

```bash
docker compose --profile monitoring stop prometheus grafana
```

오류 알림을 받으려면 `.env`의 `SENTRY_DSN`에 Sentry 프로젝트 주소를 넣습니다.

## 11. LLM 추적 (Langfuse, 선택)

질문마다 검색·답변 과정을 보고 싶을 때만 씁니다. 메모리를 2~3GB 더 쓰므로 배포 compose에는 넣지 않았습니다.

- 가장 쉬운 방법은 Langfuse Cloud(https://cloud.langfuse.com)에 프로젝트를 만들고 `.env`의 `LANGFUSE_HOST`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`를 채우는 것입니다.
- 직접 띄우려면 개발용 `infra/docker-compose.yml`의 `langfuse` 프로필 구성을 참고하세요. 그 구성은 내 컴퓨터(127.0.0.1)에서만 열리고 비밀값이 개발용 기본값이라, 서버에서 쓸 때는 비밀값을 모두 바꾸고 api·worker가 접속할 수 있는 주소로 열어야 합니다.

`.env`를 고친 뒤 `docker compose up -d`로 다시 띄우면 적용됩니다.

### 개인정보: 가명과 보관 기간

Langfuse에는 질문, 근거 원문, 답변이 그대로 남습니다. 그래서 아래처럼 다룹니다.

- **가명**: 사용자 번호와 대화 번호는 그대로 보내지 않고, `SECRET_KEY`로 만든 HMAC 가명(`u_…`, `s_…`)으로 바꿔 보냅니다. Langfuse만 봐서는 누구의 질문인지 알 수 없고, 같은 사용자의 질문끼리만 묶입니다. `SECRET_KEY`가 비어 있으면 실행할 때마다 새 키를 써서, 서버를 다시 켜면 같은 사용자라도 다른 가명이 됩니다. `SECRET_KEY`를 바꾸면 그 전 기록과 연결이 끊깁니다.
- **보관 기간 30일 (꼭 설정)**: 보관 기간은 이 서비스가 아니라 Langfuse 프로젝트 설정에서 정합니다. Langfuse 화면의 프로젝트 설정 → 데이터 보관(Data Retention)에서 **30일**로 두세요. 개인정보처리방침의 "LLM 추적 기록 30일"과 같아야 합니다. 직접 띄운 Langfuse에서 이 메뉴가 없는 판이면, Langfuse DB(ClickHouse)의 오래된 기록을 30일마다 지우는 작업을 따로 걸어야 합니다.
- **탈퇴할 때 삭제 요청**: 탈퇴하면 응답을 보낸 뒤 그 사용자 가명의 추적을 Langfuse 공개 API로 찾아(`GET /api/public/traces?userId=`) 지우도록(`DELETE /api/public/traces`) 요청합니다. 실패해도 탈퇴는 그대로 끝나고, 로그에는 HTTP 상태만 남깁니다. 추적 목록 API는 Langfuse v4에서 빠질 예정이라, 그 판에서는 삭제 요청이 실패하고 가명과 30일 보관 기간에만 기댑니다. 끄려면 `LANGFUSE_DELETE_ON_ACCOUNT_DELETE=false`. Langfuse API 키는 삭제 권한이 있는 프로젝트 키여야 합니다.
- Langfuse Cloud를 쓰면 질문이 Langfuse 회사 서버로 갑니다. 개인정보처리방침의 "처리 위탁"에 Langfuse를 적어야 합니다. 직접 띄우면 그럴 필요가 없습니다.

## 12. Ollama를 컨테이너로

서버에 Ollama를 설치하지 않고 컨테이너로 띄우는 방법입니다. CPU로만 돌아 느립니다. `.env`에서 주소를 바꿉니다.

```
OLLAMA_URL=http://ollama:11434
```

```bash
docker compose --profile ollama up -d
docker compose exec ollama ollama pull qwen3:8b
```

이후 `docker compose` 명령에는 항상 `--profile ollama`를 붙입니다.

## 13. 끄기

```bash
docker compose --profile monitoring --profile ollama down
```

데이터(볼륨)는 남습니다. `down -v`는 DB까지 모두 지우므로 쓰지 마세요.

## 문제 해결

- **https 인증서가 발급되지 않음**: `docker compose logs caddy`를 봅니다. DNS A 레코드가 이 서버 IP를 가리키는지, 80·443 포트가 열려 있는지 확인합니다. 클라우드 서버라면 보안 그룹에서도 80·443을 열어야 합니다.
- **답변이 "모델에 연결할 수 없음"**: Ollama 설정을 확인합니다. 서버에서 `curl http://localhost:11434/api/tags`가 모델 목록을 보여 주는지, 2단계의 `OLLAMA_HOST` 설정과 방화벽 규칙(11434)을 넣었는지 봅니다.
- **컨테이너가 계속 재시작됨**: `docker compose logs --tail 100 컨테이너이름`으로 오류를 봅니다. 메모리 부족이면 `.env`에서 `RERANK_MODEL`을 비우거나 `OPENSEARCH_JAVA_OPTS`를 줄입니다.
- **로그인이 유지되지 않음**: `https://`로 접속했는지 확인합니다 (`COOKIE_SECURE=true`는 https에서만 동작).

## 보안 메모

- 바깥에 열리는 포트는 Caddy의 80·443뿐입니다. DB, Redis, 검색 엔진은 Docker 내부망에서만 접속됩니다.
- Caddy가 모든 응답에 HSTS, `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `Permissions-Policy`를 붙입니다. 웹 화면에는 요청마다 새로 만든 nonce를 넣은 CSP를 붙이고, `/api`에는 백엔드가 자기 CSP를 붙입니다.
- 컨테이너는 root가 아닌 사용자로 실행됩니다.
- api는 지표를 프로세스 하나에서 세므로 `--workers 1`로 실행합니다. 늘리지 마세요.
