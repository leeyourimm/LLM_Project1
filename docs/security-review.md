# 보안 점검 보고서

- 점검 시점: 2026-10, `account-security` 브랜치 (기준: `observability` 브랜치). F16~F18 은 `auth-email` 브랜치에서, F19~F20 과 A19~A21 은 `push-2fa` 브랜치에서 더했습니다.
- 위치(파일:줄)는 수정 후 이 브랜치 기준입니다.

## 1. 범위
- 백엔드: `src/dartrag/web/` (API, 로그인·세션, CSRF, 요청 한도, 알림 API, 텔레그램 웹훅), `src/dartrag/db/repository.py`, `src/dartrag/answer/` (질문 거절·프롬프트), `src/dartrag/changes/summary.py`, `src/dartrag/feed/` (메일·텔레그램 발송, 구독 취소 토큰), `src/dartrag/dart/` (OpenDART 클라이언트), `src/dartrag/storage/`, `src/dartrag/obs/` (로그·Sentry·Langfuse), `src/dartrag/worker/`, `src/dartrag/config.py`
- 프론트엔드: `frontend/` (Next.js), 기존 정적 화면 `src/dartrag/web/static/`
- 인프라 기본값: `infra/docker-compose.yml`, `infra/db/*.sql`
- 의존성: `pyproject.toml`, `frontend/package.json`·`package-lock.json`
- 범위 밖: `infra/prod/`, `infra/terraform/`, Dockerfile, `docs/deploy.md` (다른 브랜치에서 작업 중). 리버스 프록시(Caddy)의 TLS·헤더 설정도 여기서 보지 않았습니다.

## 2. 방법
- 위 파일을 직접 읽으며 항목별로 확인: 인증·세션, CSRF 출처 검사, 요청 한도, SQL 인젝션(문자열로 만든 SQL 검색), SSRF(서버가 여는 주소가 사용자 입력에서 오는지), 저장소 경로 탈출, 로그·오류 보고 속 비밀값, 프롬프트 인젝션, 텔레그램 웹훅 인증, 구독 취소 HMAC 토큰, 이메일 인증 토큰, 비밀번호 정책·해시, 쿠키 속성, 보안 헤더·CSP, XSS(`innerHTML`, `dangerouslySetInnerHTML`), 열린 리디렉션, 의존성 취약점, Docker 기본값.
- `npm audit`로 프론트엔드 의존성을 확인했습니다. Python 은 `pip-audit` 를 쓸 수 없는 환경이라 버전 하한만 검토했습니다.
- 고친 항목은 모두 테스트를 붙였습니다 (`tests/test_web.py`, `test_repository.py`, `test_client.py`, `test_obs.py`, `test_guard.py`, `test_cache.py`, `test_diff_summary.py`, `test_doctor.py`, `test_account_email.py`, `frontend/src/lib/redirect.test.ts`, `frontend/src/lib/token.test.ts`). 실제 Postgres 에 API 를 붙여 가입 → 내보내기 → 탈퇴도 확인했고, `next start` 로 화면 응답 헤더도 확인했습니다.

## 3. 발견 사항

심각도: 높음(바로 악용 가능하거나 비밀값 유출) / 중간(조건이 맞으면 피해) / 낮음(방어 강화) / 정보

### 3.1 고친 것

| # | 심각도 | 위치 | 내용 | 조치 |
|---|---|---|---|---|
| F1 | 높음 | `src/dartrag/dart/client.py:107` | 재시도 후에도 실패한 OpenDART 요청이 httpx 오류를 그대로 올렸다. 오류 메시지에 `crtfc_key`(인증키)가 든 주소가 들어 있어 `job_runs.detail`, `disclosures.ingest_error`, `backfill_state.last_error`, 작업자 로그, `dartrag jobs status`, Sentry 로 퍼졌다. | 경로와 상태 코드만 담은 `DartHttpError` 로 바꿔 올리고 원래 예외 연결도 끊었다 (`from None`). |
| F2 | 높음 | `src/dartrag/worker/celery_app.py:28` | httpx 는 INFO 로 요청 주소를 남기는데, 작업자를 `-l info` 로 띄우면 OpenDART 인증키와 텔레그램 봇 토큰(`/bot<토큰>/sendMessage`)이 로그에 찍혔다. CLI·API 는 이미 꺼 두었지만 작업자만 빠져 있었다. | 작업자 시작 시 `httpx`, `httpcore` 로그를 WARNING 으로 올린다. |
| F3 | 중간 | `src/dartrag/obs/errors.py:17` | Sentry 정리 함수가 이메일·봇 토큰만 가리고, 메시지 속 주소의 비밀 쿼리 값(`crtfc_key`, 인증 링크 `token`, 구독 취소 `t`)은 그대로 보냈다. | 비밀 쿼리 값을 `[삭제]`로 바꾼다. |
| F4 | 중간 | `infra/docker-compose.yml:17,29,39,44` | Postgres(기본 비밀번호), Redis(비밀번호 없음), OpenSearch(보안 플러그인 끔), Qdrant 포트를 모든 주소(0.0.0.0)에 열었다. Docker 가 연 포트는 ufw 같은 방화벽을 거치지 않아, 이 파일로 서버를 띄우면 인터넷에 그대로 열린다. | 모두 `127.0.0.1` 에만 열고 이유를 주석으로 남겼다. |
| F5 | 중간 | `frontend/package.json:13` | Next.js 16.3.6 에 알려진 취약점(이미지 최적화 SSRF GHSA-cjq9-62q9-8jv4, SSG/ISR 캐시 오염 GHSA-mcj8-r9mp-w47p 등). | 16.3.8 로 올렸다. `npm audit --omit=dev` 0건. |
| F6 | 중간 | `src/dartrag/answer/prompt.py:29`, `src/dartrag/changes/summary.py:138` | 근거 원문이 `</source>` 를 닫아 지시문을 끼워 넣지 못하게 막는 처리가 소문자·정확한 표기만 바꿨다. `</SOURCE>`, `</source >`, `<source id="9">` 로 가짜 출처를 만들 수 있었다. 변경점 요약의 `<item>` 도 같았다. | 대소문자·공백과 관계없이 여는·닫는 태그를 모두 무력화(`neutralize_tags`)하고, 질문과 출처 머리글에도 적용했다. |
| F7 | 낮음 | `src/dartrag/answer/guard.py:47` | 인젝션 거절 규칙을 전각 문자(`ｉｇｎｏｒｅ`)나 보이지 않는 문자(zero-width space)로 피할 수 있었다. | 검사 전에 NFKC 정규화, 서식 문자 제거, 공백 정리를 한다. |
| F8 | 중간 | `src/dartrag/web/alerts.py:162` | 메일 속 "그만 받기" 링크(GET)를 열기만 해도 알림이 꺼졌다. 기업 메일의 보안 검사기가 링크를 미리 열면 사용자 모르게 알림이 꺼진다. | GET 은 확인 버튼만 보여 주고, 실제 끄기는 POST(버튼, 메일 앱 원클릭 RFC 8058)로만 한다. |
| F9 | 낮음 | `frontend/src/app/login/AuthForm.tsx:16`, `frontend/src/lib/redirect.ts` | 로그인 뒤 이동 주소 `?next=` 검사가 `//` 만 막아, 브라우저가 `//evil.com` 으로 읽는 `/\evil.com` 이 통과할 수 있었다 (열린 리디렉션). | `safeNext` 로 역슬래시·제어 문자까지 막는다. |
| F10 | 낮음 | `frontend/next.config.ts:25` | Next 화면 응답에 보안 헤더가 없어 다른 사이트가 iframe 으로 감쌀 수 있었다 (클릭재킹). | `X-Frame-Options: DENY`, `frame-ancestors 'none'`, nosniff, Referrer-Policy, COOP, Permissions-Policy 를 붙인다. |
| F11 | 낮음 | `src/dartrag/web/app.py:107,112` | API 응답에 캐시 지시가 없어 공용 PC·프록시가 대화 기록·관심 종목·내 데이터를 남길 수 있었고, HSTS 가 없었다. | `/api/*` 에 `Cache-Control: no-store`(따로 정한 응답 제외), `COOKIE_SECURE=true` 일 때 HSTS, COOP, Permissions-Policy 를 붙인다. |
| F12 | 낮음 | `src/dartrag/web/app.py:94` | CSRF 검사가 `Origin` 헤더가 없으면 통과시켰다 (주 방어는 `SameSite=Lax` 쿠키라 바로 악용되지는 않음). | `Origin` 이 없을 때 `Sec-Fetch-Site: cross-site` 면 막는다. 브라우저가 아닌 요청(텔레그램 웹훅, 메일 앱 원클릭)은 둘 다 없어 영향 없음. |
| F13 | 낮음 | `src/dartrag/web/app.py:163` | 로그아웃 때 쿠키를 지우는 응답에 `HttpOnly`·`SameSite`·`Secure` 속성이 빠져 있었다. | 만들 때와 같은 속성으로 지운다 (탈퇴도 같은 함수). |
| F14 | 정보 | `src/dartrag/doctor.py:201`, `src/dartrag/web/asgi.py:23` | 공개 서버에서 위험한 설정(로그인 꺼짐, https 인데 `COOKIE_SECURE=false`, 짧은 `SECRET_KEY`, `/metrics` 토큰 없음, 짧은 웹훅 비밀값)을 알려 주는 곳이 없었다. | `dartrag doctor` 와 API 시작 로그에서 경고한다. 시작을 막지는 않는다. |
| F15 | 중간(개인정보) | `src/dartrag/web/app.py:245,257`, `src/dartrag/db/repository.py:537` | 개인정보처리방침은 "탈퇴·열람을 직접 할 수 있다"고 하지만 기능이 없었다. | `DELETE /api/account`(지금 비밀번호 확인, `rate("auth")`, 쿠키 삭제, 한 트랜잭션에서 모든 사용자 기록 삭제)와 `GET /api/account/export` 를 추가했다. 사용자 외래 키가 모두 `ON DELETE CASCADE` 인지 DB 테스트로 확인한다. |
| F16 | 낮음 | `src/dartrag/web/app.py:283,370,382`, `src/dartrag/web/alerts.py:62` | 가입할 때 이메일 소유를 확인하지 않았다 (예전 A4). 남의 주소로 가입한 계정도 그 주소의 주인처럼 보였다. | 가입하면 인증 링크(한 번 쓰는 토큰, SHA-256 해시만 저장, 24시간)를 보낸다. `EMAIL_VERIFICATION_REQUIRED=true` 면 인증 전에는 화면에 안내 띠를 보이고 이메일 알림을 켤 수 없다 (403). 인증 메일 다시 받기는 `rate("auth")` 와 1분에 1번으로 제한한다. 메일 발송이 없으면 인증할 방법이 없어 강제하지 않고 `doctor` 가 알려 준다. |
| F17 | 중간 | `src/dartrag/web/app.py:322,347`, `src/dartrag/db/repository.py:664` | 비밀번호 재설정(메일) 기능이 없어, 잊으면 운영자가 `dartrag user password` 로 바꿔야 했다 (예전 남은 위험). | `POST /api/auth/password/forgot` 는 가입 여부와 관계없이 같은 응답(202, 같은 문구)을 돌려주고 메일은 응답 뒤에 보낸다. 토큰은 `secrets.token_urlsafe(32)`, 해시만 저장, 30분, 한 번만 쓰고(만료돼도 지움), 새로 요청하면 예전 링크는 무효가 된다. 재설정하면 모든 세션과 남은 재설정 링크를 한 트랜잭션에서 지우고 로그인 잠금도 푼다. 비밀번호 정책 검사는 토큰을 쓰기 전에 한다. 요청은 `rate("auth")`(접속 주소별)에 더해 받는 주소별로 1시간에 3통까지만 보낸다 (넘어도 응답은 같음, Redis 키에는 주소 해시만). 링크는 웹 화면 `/reset-password` 로 가고 화면이 토큰을 POST 본문으로 보내므로 API 접속 로그에 남지 않으며, 화면은 주소창에서 토큰을 지운다. |
| F18 | 낮음 | `src/dartrag/web/app.py:242`, `src/dartrag/web/auth.py:140` | 다른 사람이 내 계정에 로그인해도 알 방법이 없었다 (예전 남은 위험: 새 기기 로그인 알림 없음). | 로그인할 때 브라우저 종류·OS·접속 네트워크(IPv4 /24, IPv6 /64)의 HMAC(`SECRET_KEY`)만 `user_devices` 에 저장하고, 처음 보는 조합이면 인증된 주소로 알림 메일(시각, 브라우저, 네트워크 대역, 재설정 링크)을 보낸다. User-Agent 원문과 IP 는 저장하지 않는다. |
| F19 | 중간 | `src/dartrag/web/two_factor.py`, `src/dartrag/web/totp.py`, `src/dartrag/web/app.py:342,367,438,519`, `infra/db/014_two_factor.sql` | 2단계 인증이 없어 비밀번호가 새거나 메일함을 빼앗기면(재설정 링크) 계정도 빼앗겼다 (예전 남은 위험). | 인증 앱 코드(TOTP, RFC 6238: SHA-1, 6자리, 30초, 앞뒤 한 구간 허용)를 켤 수 있다. 켜기는 지금 비밀번호 확인 → QR·직접 입력 키 → 첫 코드 확인 순서이고, 켜면 다른 기기 세션을 모두 끊는다. 키는 `SECRET_KEY` 에서 HKDF 로 만든 키로 AES-GCM 암호화해 저장하고(사용자 번호를 AAD 로 묶어 다른 줄로 옮겨 쓰지 못함), 복구 코드 10개(약 59비트씩)는 사용자 번호를 섞은 SHA-256 해시만 저장해 한 번씩만 쓴다. 쓴 시간 구간을 기억해(`last_step`) 같은 코드를 다시 쓰지 못한다. 로그인은 비밀번호를 맞히면 세션 대신 두 번째 단계 쿠키(`dartrag_2fa`: HttpOnly, SameSite=Strict, Path=/api/auth, 5분, DB 에는 해시만)를 주고, `POST /api/auth/login/2fa` 에서 코드를 받는다. 틀린 코드는 그 단계에서 5번까지, 계정 기준 로그인 시도 제한에도 함께 세고 `rate("auth")` 가 걸린다. 끄기·복구 코드 새로 받기·탈퇴는 비밀번호와 코드를 모두 요구한다. 메일 비밀번호 재설정은 2단계 인증을 끄지 않는다: 재설정한 기기도 코드를 넣어야 로그인된다. 휴대폰과 복구 코드를 모두 잃으면 운영자가 본인 확인 뒤 `dartrag user 2fa-off` 로 끈다. 내 데이터에는 사용 여부·켠 시각·남은 복구 코드 수만 넣고, 탈퇴하면 모두 지운다. |
| F20 | 낮음 | `src/dartrag/feed/webpush.py`, `src/dartrag/feed/channels.py:119`, `src/dartrag/web/alerts.py:198`, `src/dartrag/obs/errors.py:19`, `infra/db/013_web_push.sql` | 웹 푸시 알림을 더하면서 서버가 사용자가 보낸 주소(구독 endpoint)로 요청을 보내게 됐다 (새 SSRF 경로). | 구독 주소는 https, 443 포트, 사용자 정보 없음, 2048자 이하이고 알려진 푸시 서비스 호스트(`fcm.googleapis.com`, `updates.push.services.mozilla.com`, `web.push.apple.com`, `*.push.apple.com`, `*.notify.windows.com` 등)일 때만 등록하고, 보낼 때 다시 확인한다. 리디렉션은 따르지 않고 2xx 가 아니면 실패로 본다. 알림 내용은 RFC 8291(aes128gcm)로 그 브라우저 키에 맞춰 암호화하고 VAPID(RFC 8292, ES256) 서명을 붙인다. 내용은 제목·짧은 요약·열 주소(DART 원문 또는 `/feed`)만이며 3000바이트를 넘지 않는다. 404·410 응답과 예전 서버 키로 만든 구독은 바로 지운다. 화면과 내 데이터에는 구독 주소 대신 주소의 SHA-256 과 푸시 서비스 호스트만 내보내고, 오류 메시지에는 주소를 넣지 않으며 Sentry 정리 함수가 푸시 주소의 경로를 지운다. VAPID 비밀키는 `dartrag push keys` 가 `.env` 에만 쓰고(새 파일은 600) 화면·로그에 내지 않는다. 서비스 워커는 알림을 누를 때 이 사이트와 `https://dart.fss.or.kr` 주소만 연다. |

### 3.2 받아들인 것 (이유)

| # | 심각도 | 위치 | 내용 | 받아들인 이유 |
|---|---|---|---|---|
| A1 | 중간 | `src/dartrag/web/auth.py:21` | scrypt 매개변수 N=2^14, r=8 (메모리 16MB)은 OWASP 권장(N=2^17)보다 낮다. | 작은 서버에서 로그인마다 128MB 를 쓰기 어렵다. 해시 문자열에 매개변수가 들어 있어 나중에 올려도 예전 해시를 검증할 수 있고, 로그인 시도 제한이 있다. 다음 단계로 로그인 성공 시 더 강한 매개변수로 다시 해시하는 것을 권장한다. |
| A2 | 낮음 | `src/dartrag/web/auth.py:16` | 비밀번호 정책은 길이(10~128자)만 본다. 유출된 비밀번호 목록 확인이 없다. | 길이 위주 정책은 NIST SP 800-63B 와 맞다. 유출 목록 확인은 외부 API 호출이나 큰 목록 파일이 필요하다. |
| A3 | 중간 | `src/dartrag/web/auth.py:87` | 로그인 시도 제한이 프로세스 메모리에 있어 재시작하면 초기화되고, 서버를 여러 대 두면 따로 센다. 남의 이메일로 5번 틀려 15분 동안 막는 것도 가능하다. | API 는 `--workers 1` 로 운영한다. IP 기준 제한(계정 기준의 4배)이 함께 있고, 잠금은 15분이면 풀린다. 서버를 늘릴 때 Redis 로 옮긴다. |
| A4 | - | - | ~~가입할 때 이메일 소유를 확인하지 않는다.~~ | F16 에서 고쳤다. |
| A5 | 낮음 | `src/dartrag/web/app.py:291` | `METRICS_TOKEN` 이 없으면 `/metrics` 가 열려 있다. | 공개 서버에서는 프록시가 막는 것을 전제로 하고, 이제 `doctor` 와 시작 로그가 경고한다. 지표에는 개인정보가 없다. |
| A6 | 낮음 | `src/dartrag/web/app.py:78` | `/api/docs`, `/api/openapi.json` 이 공개돼 있다. | API 목록만 드러나고 호출에는 로그인이 필요하다. |
| A7 | 낮음 | `frontend/src/app/layout.tsx`, `infra/prod/Caddyfile` | Next 화면은 테마 초기화 인라인 스크립트 때문에 `script-src` CSP 를 걸지 않았다. | 배포 구성에서 해결: Caddy 가 요청마다 nonce 를 만들어 `script-src 'self' 'nonce-…'` CSP 를 붙이고, Next 와 layout.tsx 가 같은 nonce 를 스크립트에 단다 (화면은 요청마다 그려진다). 개발 서버(`next dev`)에는 걸리지 않는다. |
| A8 | 낮음 | `src/dartrag/web/alerts.py:140` | 이메일 인증 링크는 GET 으로 바로 인증된다. 보안 검사기가 미리 열어도 인증된다. | 인증 메일은 로그인한 계정의 이메일로만 보내므로, 그 메일함에 도착했다는 것 자체가 소유 확인이다. |
| A9 | 정보 | `src/dartrag/feed/alerts.py:72` | 구독 취소 HMAC 토큰에 만료가 없다. | 메일 앱 원클릭 구독 취소(RFC 8058) 관행이다. 토큰으로 할 수 있는 일은 그 채널 알림 끄기뿐이다. 비교는 `hmac.compare_digest`, 비밀값이 없으면 항상 거절한다. |
| A10 | 낮음 | `src/dartrag/dart/client.py:201` | 고유번호 목록 XML 을 lxml 기본 파서로 읽는다. | lxml 5 기본값은 외부 엔티티·네트워크 접근을 막고, 내용은 HTTPS 로 받은 OpenDART 응답이다. 공시 원문 파서는 `resolve_entities=False` 를 쓴다. |
| A11 | 중간 | `infra/docker-compose.yml:15,64,91` | 개발용 compose 에 기본 비밀번호(Postgres `dartrag`, Grafana `admin`, Langfuse 암호화 키 0…)가 있다. | 개발용이며 이제 모든 포트가 127.0.0.1 에만 열린다. 공개 서버 설정은 `infra/prod/` 에서 비밀값을 바꿔 쓴다 (범위 밖, 따로 확인 필요). |
| A12 | 낮음 | `pyproject.toml:6` | Python 의존성은 하한만 있고 잠금 파일이 없다. 이 환경에서 `pip-audit` 를 돌리지 못했다. | 고침: `requirements/` 에 해시까지 고정한 잠금 파일(배포용 `runtime.txt`·`torch.txt`, CI용 `dev.txt`)을 두고 `make lock`(uv)으로 만든다. 백엔드 이미지와 CI 는 `--require-hashes` 로 잠금 파일에서만 설치한다. CI `audit` 작업이 `pip-audit` 로 잠금 파일을 검사하고, 알려진 취약점이 있으면 실패한다. PyTorch 는 CPU 전용 색인에서 받아 버전만 고정한다(해시 없음). Dependabot 이 매주 업데이트 PR 을 연다. |
| A13 | 낮음 | `frontend/package.json:24` | 개발용 vitest 3.2.4 가 쓰는 tinypool·@vitest/mocker 에 알려진 취약점이 있다. | 고침: vitest 5.0.3 으로 올리고 peer 의존성인 vite 8 을 명시했다. `npm audit` 결과 0건. CI `audit` 작업이 `npm audit --omit=dev --audit-level=high` 로 배포물 패키지를 검사한다. |
| A14 | 정보 | `src/dartrag/answer/guard.py:19` | 인젝션 거절은 규칙(정규식) 기반이라 새 표현은 놓친다. | 구조적 방어가 함께 있다: 모델에 도구·DB 권한이 없고 출력은 화면에 텍스트로만 나가며, 원문은 태그 안 자료로만 다루고, 인용·숫자 검증이 붙는다. 적대적 문항은 배포 기준(95%)으로 계속 잰다. |
| A15 | 정보 | `src/dartrag/web/alerts.py:51` | 인증 메일 재전송 제한(60초)이 프로세스 메모리에 있다. | 같은 사용자의 주소로만 보내므로 남용 피해가 작고, 서버는 한 대로 운영한다. |
| A16 | 낮음 | `src/dartrag/web/auth.py:120` | 새 기기 판단이 브라우저 종류·OS·네트워크 대역만 본다. 같은 대역에서 같은 브라우저를 쓰는 공격자는 알리지 않고, 이동 통신망이 바뀌면 같은 기기도 새 기기로 알린다. 기기 기록이 없던 계정(가입 직후, 이 기능 전에 만든 계정)의 첫 로그인과 인증하지 않은 주소에는 알리지 않는다. | 브라우저 버전·IP 끝자리까지 보면 같은 기기가 매번 새 기기가 되어 알림을 무시하게 된다. 인증하지 않은 주소로 보내지 않는 것은 남의 주소로 가입해 알림 메일을 쏟아내지 못하게 하려는 것이다. 알림은 탐지 보조 수단이다. |
| A17 | 낮음 | `frontend/src/app/verify-email/VerifyEmail.tsx` | 가입 인증 화면은 열면 바로 인증한다. 스크립트를 실행하는 메일 보안 검사기가 열어도 인증된다. | A8 과 같다: 인증 메일은 그 주소로만 가므로 메일함에 도착한 것 자체가 소유 확인이다. 비밀번호 재설정은 화면에서 새 비밀번호를 넣어야만 토큰을 쓴다. |
| A18 | 정보 | `src/dartrag/web/app.py:47` | 받는 주소별 재설정 메일 제한(1시간에 3통)과 인증 메일 다시 받기 제한은 Redis 가 없으면 서버 안 메모리에서 센다. | 다른 요청 한도와 같은 `RateLimiter` 를 쓴다. 서버는 한 대로 운영하고, 접속 주소별 `rate("auth")` 가 함께 걸린다. |
| A19 | 낮음 | `src/dartrag/web/totp.py:147` | 2단계 인증 키를 `SECRET_KEY` 로 암호화하므로 `SECRET_KEY` 를 바꾸면 기존 키를 풀 수 없다. | 그때는 인증 앱 코드가 맞지 않고(로그에 경고), 복구 코드로 들어와 끄고 다시 켜야 한다. 복구 코드도 없으면 운영자가 `dartrag user 2fa-off` 로 끈다. `SECRET_KEY` 를 바꿀 때 알아야 할 점으로 `docs/deploy.md` 에 적었다. DB 백업만 빼앗긴 경우에는 코드를 만들 수 없다. |
| A20 | 낮음 | `frontend/src/app/watchlist/PushRow.tsx` | 웹 푸시 구독은 로그아웃해도 남아, 공용 PC 에서 켜 두면 다음 사람이 알림 제목을 볼 수 있다. | 알림에는 공시 제목 요약만 있고 계정 정보는 없다. 로그아웃은 서비스 워커의 구독을 지울 수 없을 때가 있어(다른 탭, 꺼진 화면) 일부러 남기고, 화면에서 "로그아웃해도 알림이 온다"고 알리며 다른 기기에서도 목록으로 해제할 수 있다. |
| A21 | 정보 | `src/dartrag/feed/alerts.py:93` | 웹 푸시는 여러 브라우저 가운데 한 곳에라도 보냈으면 알림을 보낸 것으로 기록한다. | 실패한 브라우저만 다시 보내면 성공한 브라우저가 같은 알림을 또 받는다. 끝난 구독(404·410)은 지우므로 계속 실패하는 곳은 남지 않는다. |

### 3.3 확인했고 문제 없던 것
- SQL 인젝션: 모든 쿼리가 psycopg 매개변수(`%s`)를 쓴다. 문자열로 만든 SQL 은 `repository.py` 의 `watchlist()` 하나인데, 끼워 넣는 값이 코드 상수(표 이름)뿐이다.
- SSRF: 서버가 여는 주소(OpenDART, 텔레그램, Ollama, Qdrant, OpenSearch, Langfuse, SMTP)는 모두 설정값이고 사용자 입력에서 오지 않는다. 사용자가 보내는 주소는 웹 푸시 구독 주소 하나이며 알려진 푸시 서비스 호스트만 받는다 (F20). 출처 링크는 화면에 링크로만 보여 준다.
- 경로 탈출: 원문 저장소 키는 `resolve()` 후 저장소 폴더 안인지 확인한다 (`src/dartrag/storage/raw.py:21`). 키는 접수번호로 만든다.
- 세션: 토큰은 `secrets.token_urlsafe(32)`, DB 에는 SHA-256 해시만 저장, 만료 확인, 비밀번호 변경·탈퇴 시 모든 세션 삭제. 쿠키는 `HttpOnly`, `SameSite=Lax`, `COOKIE_SECURE` 일 때 `Secure`.
- 로그인: 없는 계정도 같은 해시 계산을 해 응답 시간으로 가입 여부를 알 수 없고, 오류 문구도 같다.
- 텔레그램 웹훅: 비밀값이 없으면 항상 403, `X-Telegram-Bot-Api-Secret-Token` 을 상수 시간 비교한다. 연결은 1:1 대화방에서 한 번 쓰는 코드(해시 저장, 30분 만료)로만 된다.
- 이메일 인증 토큰: `secrets.token_urlsafe(24)`, 해시만 저장, 24시간 만료, 한 번 쓰면 지운다.
- 계정 메일 로그: 받는 주소, 링크(토큰), SMTP 오류 내용은 로그에 남기지 않고 실패한 예외 종류만 WARNING 으로 남긴다. 링크 쿼리의 `token` 은 Sentry 정리 함수가 이미 지운다.
- 메일 헤더 주입: 이메일 형식 검사가 공백·줄바꿈을 허용하지 않고, `EmailMessage` 도 줄바꿈이 든 헤더를 거부한다.
- XSS: 기존 정적 화면은 엄격한 CSP(`script-src 'self'`), 알림 안내 페이지는 `html.escape`, Next 화면은 React 이스케이프와 배포 시 nonce CSP(Caddy).
- 사용자별 데이터 접근: 대화·평가·관심 종목·알림 채널은 모두 `user_id` 조건으로만 읽고 쓴다 (다른 사용자 대화 조회·이어 묻기·평가가 404).

## 4. 남은 위험
- **탈퇴 뒤에도 남는 사본**: DB 백업과 서버·프록시 접속 로그(IP)에는 탈퇴 뒤에도 기록이 남는다. 백업은 30일 안에 순환한다.
- **Langfuse 추적**: 질문·근거·답변 원문이 남는다. 사용자·대화 번호는 `SECRET_KEY` HMAC 가명으로만 보내므로 Langfuse 만으로는 누구인지 알 수 없고, 탈퇴하면 그 가명의 추적 삭제를 요청한다(`obs/tracing.py` `forget_user`, 실패해도 탈퇴는 끝남). 남은 위험: (1) 보관 기간 30일은 Langfuse 프로젝트 설정에서 운영자가 직접 켜야 한다 (`docs/deploy.md` 11절). (2) 삭제는 추적 목록 API 에 기대는데 이 API 는 Langfuse v4 에서 빠질 예정이라, 그 뒤에는 가명과 보관 기간에만 기댄다. (3) 질문 본문에 사용자가 직접 쓴 개인정보는 가명으로 가려지지 않는다. (4) `SECRET_KEY` 를 아는 사람은 사용자 번호로 가명을 다시 만들 수 있다.
- **공개 배포 설정**: 리버스 프록시의 TLS·HSTS·`/metrics` 차단, 운영용 비밀값, 방화벽은 `infra/prod/`·`infra/terraform/` 에서 확인해야 한다 (이번 범위 밖).
- **Redis 가 멈추면** 요청 한도가 서버 안 메모리로 바뀌어 서버마다 따로 센다. 작업 잠금과 OpenDART 호출 한도 공유도 멈춘다.
- **로그인 보호**: 2단계 인증(F19)은 사용자가 켜야 하며 강제하지 않는다. 켜지 않은 사용자는 메일함을 빼앗기면 재설정 링크로 계정도 빼앗길 수 있다. 메일 발송(SMTP)을 설정하지 않은 서버에서는 비밀번호 재설정·이메일 인증·새 기기 알림이 꺼지고, 비밀번호를 잊으면 운영자가 `dartrag user password` 로 바꿔야 한다 (`dartrag doctor` 가 알려 준다). 2단계 인증은 인증 앱 코드라 피싱 사이트가 실시간으로 코드를 받아 쓰는 공격은 막지 못한다 (패스키·WebAuthn 은 아직 없음).
- **LLM 출력**: 근거 규칙과 경고가 있어도 답변이 틀릴 수 있다. 화면마다 "투자 권유 아님" 고지를 유지한다.
- **비밀값 보관**: `.env` 평문 파일에 OpenDART 키, 봇 토큰, SMTP 비밀번호, `SECRET_KEY` 가 있다. 서버에서는 파일 권한을 600 으로 두고, 가능하면 비밀값 관리 서비스를 쓴다.
- **의존성**: 잠금 파일과 CI 취약점 검사(A12), vitest 5 업그레이드(A13)로 고쳤다. 남은 것: PyTorch CPU 판은 해시를 고정하지 않았고, `pip-audit` 는 심각도를 구분하지 못해 낮은 등급 취약점에도 실패한다(필요하면 `--ignore-vuln` 으로 근거와 함께 예외를 둔다).
