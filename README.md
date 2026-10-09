# DART 공시 분석 서비스

상장사 공시를 수집해서 **근거를 대며 답하고, 수치를 정확히 보여주고, 변화가 생기면 알려주는** 서비스입니다.
전체 설계는 [docs/design.md](docs/design.md)에 있습니다.
AWS 서버 한 대에 배포하는 방법은 [docs/terraform.md](docs/terraform.md)에 있습니다.
주요 기술 선택의 이유는 [의사결정 기록(ADR)](docs/adr/README.md)에, 보안 점검 결과는 [docs/security-review.md](docs/security-review.md)에 있습니다.

> 이 서비스는 공시 정보의 검색·요약을 제공하며 투자 권유가 아닙니다.

## 진행 상황
기능은 모두 코드로 만들었고, 남은 일은 실제 OpenDART 데이터로 검증하는 것(2026-10-12 이후)과 공개입니다.

| 단계 | 내용 | 상태 |
|---|---|---|
| 0. 기반 | 저장소, Docker 인프라, CI, OpenDART 수집기, 재무 DB | ✅ |
| 1. 핵심 RAG | 파싱, 청킹, 하이브리드 검색, 출처 답변, 대화, 검색 품질 개선 | ✅ |
| 2. 정확도 | 숫자·인용 검증기, 재무 DB 조회, 계산기 | ✅ |
| 3. 평가 체계 | 채점기, 문항 자동 생성, 배포 기준(릴리스 게이트) | ✅ |
| 4. 변화 추적 | 변경점 비교, LLM 변경점 요약, 공시 피드, 이메일·텔레그램 알림 | ✅ |
| 5. 제품화 | Next.js 화면, API, 회사 대시보드, 기업 비교, PDF 리포트, 로그인·계정 | ✅ |
| 6. 확장 | Celery 작업자, 전 상장사 수집, 신규 공시 30분 반영 파이프라인 | ✅ |
| 7. 운영 | 관측(지표·Langfuse·Sentry), 보안 점검, Docker·HTTPS 배포, Terraform, 브라우저 종단 테스트 | ✅ |
| 실데이터 검증 | OpenDART 실데이터로 수집·답변·수치·알림 확인 (2026-10-12 이후) | ⬜ |
| 평가셋 | 실데이터로 만든 300문항, 정기 회귀 검사 기준선 | ⬜ |
| 공개 | 베타 공개 | ⬜ |

## 한 번에 실행하기
처음 한 번은 아래 "빠른 시작"의 1, 2번으로 설치를 먼저 하세요. 그다음부터는 Docker Desktop과 Ollama 앱을 켠 뒤, 프로젝트 폴더에서 아래 두 줄을 차례로 실행하면 됩니다.
```bash
dartrag doctor
dartrag run
```
`dartrag doctor`는 인증키, Postgres, 검색 서비스, 임베딩 라이브러리, Ollama 모델, 저장 공간을 점검하고 빠진 것마다 고칠 명령을 한 줄로 알려줍니다. 모두 ✅가 되면 `dartrag run`이 수집 → 파싱 → 색인 → 공시 피드 → 평가를 차례로 실행하고 단계별 결과와 걸린 시간을 요약합니다. 기본 15개사, 2022~2024년이며 처음에는 원문과 임베딩 모델을 내려받느라 오래 걸립니다. 평가 문항 수는 `--eval-limit`로 바꾸고, `--eval-limit 0`이면 평가를 건너뜁니다. 끝나면 `dartrag serve`로 웹 화면을 엽니다.

## 인터넷에 공개할 때 (로그인)
내 컴퓨터에서만 쓸 때는 로그인이 필요 없습니다 (기본값). 다른 사람이 접속하게 하려면 `.env`에서 아래처럼 바꾸고 서버를 다시 켭니다.
```
AUTH_REQUIRED=true
ALLOW_SIGNUP=false
COOKIE_SECURE=true
```
`ALLOW_SIGNUP=false`이면 아무나 가입할 수 없고, 운영자가 계정을 직접 만듭니다. 비밀번호는 화면에 보이지 않게 입력받습니다.
```bash
dartrag user add me@example.com
```
- 로그인을 켜면 공개 API는 `/api/health`와 로그인 관련 API뿐이고, 나머지는 로그인해야 쓸 수 있습니다.
- 비밀번호는 scrypt 해시로만 저장하고, 세션 토큰도 해시로만 저장합니다. 비밀번호를 바꾸면 그 계정의 다른 로그인은 모두 끊깁니다.
- 같은 계정이나 같은 IP에서 로그인을 여러 번 틀리면 15분 동안 막습니다.
- 관심 종목은 사용자마다 따로 저장됩니다. 웹훅 알림은 운영자 목록(`dartrag watch`) 기준입니다.
- `COOKIE_SECURE=true`는 https로 서비스할 때만 켜세요. http에서 켜면 로그인이 유지되지 않습니다. 리버스 프록시 뒤에서 쓸 때는 실제 접속 IP가 보이도록 `uvicorn`의 `--proxy-headers` 설정이 필요합니다.

## 서버에 배포하기
리눅스 서버 한 대에 Docker로 https 서비스를 띄우는 방법(처음 실행, 상태 확인, 로그, 업데이트, 백업·되돌리기, 모니터링)은 [docs/deploy.md](docs/deploy.md)에 있습니다. 설정 파일은 `infra/prod/`에 있습니다.

## 빠른 시작
명령어는 한 줄씩 실행하세요. (macOS 기본 셸 zsh는 줄 끝 `#` 주석을 인식하지 않습니다.)

1. `.env` 파일을 만들고 `DART_API_KEY=` 뒤에 인증키를 넣습니다 ([OpenDART](https://opendart.fss.or.kr)에서 발급).
```bash
cp .env.example .env
```
2. 설치하고 인프라(Postgres, Redis)를 띄웁니다 (Docker Desktop 실행 필요). 검색용 Qdrant·OpenSearch는 1단계부터 `make up-search`로 띄웁니다. Postgres 스키마는 첫 기동 때 자동 적용되고, 다시 적용하려면 `make migrate`를 실행합니다.
```bash
python3 -m venv .venv
source .venv/bin/activate
make install
make up
```
3. 수집합니다. 옵션 없이 실행하면 기본 15개사, 2022~2024년입니다.
```bash
dartrag collect -s 005930 --start-year 2024 --end-year 2024
dartrag collect
```
4. 수집한 원문을 섹션·표 단위 청크로 나눕니다.
```bash
dartrag parse
```
5. 검색 인덱스를 만들고 검색해봅니다. 임베딩 모델(bge-m3, 약 2.3GB)은 처음 실행할 때 내려받습니다.
```bash
pip install -e ".[embed]"
make up-search
dartrag index
dartrag search "HBM 매출 비중" -s 005930
```
6. 공시를 근거로 질문에 답합니다. 답변은 내 컴퓨터의 [Ollama](https://ollama.com) 로컬 모델이 만듭니다. Ollama 앱을 설치해 실행한 뒤 모델을 받습니다 (기본 `qwen3:8b`, 약 5GB. `.env`의 `LLM_MODEL`로 바꿀 수 있음). Mac에서는 Docker 대신 Ollama 앱으로 실행해야 GPU를 씁니다.
```bash
ollama pull qwen3:8b
dartrag ask "2024년 HBM 매출 비중은?" -s 005930
```

## 0단계에서 한 것
- **OpenDART 클라이언트** (`src/dartrag/dart/client.py`): 고유번호, 공시 목록(페이지네이션, 최종 보고서만), 원문 zip, 단일회사 전체 재무제표. 호출 간격 제한, 요청 제한 초과(020)·점검(800)·5xx 재시도, 데이터 없음(013)은 빈 결과로 처리
- **보고서 해석** (`dart/reports.py`): `[기재정정]사업보고서 (2023.12)` → 종류, 기간, 정정 여부, 보고서 코드. 결산월이 12월이 아닌 회사도 분기 코드를 올바르게 계산
- **금액 정규화** (`normalize.py`): 쉼표, 괄호·△ 음수, 단위 → 원 단위 정수. 해석할 수 없는 값은 조용히 넘기지 않고 오류
- **원문 저장소** (`storage/raw.py`): 로컬 또는 S3 호환 저장소. 이미 받은 원문은 다시 받지 않음
- **재무 DB** (`infra/db/001_init.sql`): 기업, 공시, 재무 계정. 연결/별도 구분, 원본 문자열 보관, 정정공시 시 해당 보고서 수치 통째 교체
- **수집 파이프라인** (`pipeline/collect.py`): 상장사 동기화 → 정기공시 → 원문 → 재무제표(연결, 별도)

## 1단계에서 한 것
- **원문 파서** (`parsing/document.py`): DART XML의 섹션 경로, 표(병합 셀 펼침), 단위, 표 제목 추출. 깨진 XML도 복구해서 읽음
- **청킹** (`parsing/chunking.py`): 섹션 경계를 넘지 않게 묶고, 긴 표는 머리글을 반복해 나눔. 청크마다 "회사 / 보고서 / 섹션" 맥락 문장을 붙임
- **하이브리드 검색** (`search/`): Qdrant 벡터 검색(bge-m3)과 OpenSearch 키워드 검색(nori 형태소 분석)을 RRF로 합침. 회사·연도·보고서 종류 필터
- **출처 답변** (`answer/`): 검색한 발췌에 번호를 붙여 로컬 LLM(Ollama)에 넘기고, 문장마다 출처 번호를 달게 함. 없는 번호를 인용하거나 출처 표시가 없으면 경고. 근거가 없으면 지어내지 않고 "찾지 못했습니다"로 답함
- **색인 파이프라인** (`pipeline/index.py`): 새로 파싱됐거나 임베딩 모델이 바뀐 공시만 다시 색인

## 2단계에서 한 것
- **숫자 검증** (`answer/numbers.py`): 답변 문장 속 금액·비율을 그 문장이 인용한 원문과 대조. 조·억 표기와 표 단위(백만원 등)를 원 단위로 환산하고 답에 적힌 자릿수만큼 반올림을 허용. 원문에서 확인되지 않은 숫자는 경고
- **재무 DB 조회와 계산기** (`finance/`): 질문에서 회사·연도·보고서(연간, 분기)·지표(매출, 영업이익, 순이익, 자산 등)와 비율(영업이익률, 부채비율 등)을 알아내 재무 DB에서 값을 찾고, 증감률과 비율은 코드가 계산. 결과를 첫 번째 출처로 넣어 모델이 숫자를 직접 계산하지 않게 하고, 숫자 검증도 그대로 적용

## 3단계에서 한 것
- **평가 체계** (`eval/`, `src/dartrag/eval/`): 문항을 JSONL로 관리하고 규칙으로 채점 (검색 적중, 정답 숫자, 핵심 키워드, 출처 표시, 답이 없는 질문에 "못 찾음"으로 답하는지). 로컬 모델로 채점하면 점수가 흔들려 회귀 테스트로 쓰기 어려워서 LLM 채점은 쓰지 않음
- **문항 자동 생성**: 재무 DB 값과 계산기로 정답이 확실한 숫자 문항(금액, 증감률, 영업이익률)과 답하면 안 되는 문항을 만듦. `eval/manual.jsonl`에는 서술형·답 없는 질문 34개
- 실행: `dartrag eval generate` 후 `dartrag eval run eval/manual.jsonl eval/generated.jsonl` → `reports/eval/<시각>/report.md`. `--min-pass-rate 0.8`을 주면 기준 미달 시 실패로 끝나 회귀 검사에 쓸 수 있음

## 4단계에서 한 것
- **보고서 변경점 비교** (`changes/`): 같은 회사의 두 보고서를 섹션별로 비교해 추가·삭제·수정된 문단과 표 행을 찾음. 목차 번호가 바뀌어도 섹션 이름으로 짝짓고, 숫자만 갱신된 문장은 따로 모아 잡음을 줄임. 위험·소송·최대주주·배당 같은 섹션 변화를 먼저 보여줌
- 실행: `dartrag diff -s 005930` (최근 두 사업보고서, 정정공시 반영) 또는 `dartrag diff --old <접수번호> --new <접수번호> --out diff.md`
- **주요 공시 피드** (`feed/`, `pipeline/feed.py`): OpenDART의 주요사항보고·거래소공시를 받아 제목으로 28가지 사건(유상증자, 전환사채, 합병·분할, 소송, 최대주주 변경, 자사주, 공급계약, 잠정실적 등)과 중요도(🔴3 🟠2 ⚪1)로 분류해 저장. 정정공시도 따로 표시
- **관심 종목 알림**: `dartrag watch add 005930` 후 `dartrag feed poll --every 600`을 켜두면 10분마다 새 공시를 확인해 기준 중요도 이상이면 알림. `.env`의 `ALERT_WEBHOOK_URL`에 Slack·Discord 웹훅을 넣으면 그쪽으로, 비워 두면 터미널로 보냄. 등록 이전 공시는 보내지 않고, 같은 공시는 한 번만 보냄. `dartrag feed show --days 7`로 최근 주요 공시 목록 확인

## 5단계에서 한 것
- **웹 화면** (`web/`): `dartrag serve` 후 http://127.0.0.1:8000 에서 질문하기(답변 속 [1]을 누르면 해당 출처 원문이 펼쳐짐, 숫자·출처 경고 표시), 공시 피드, 관심 종목 관리, 보고서 변경점 비교. 빌드 도구 없는 정적 HTML·JS라 Node 설치가 필요 없음. 다크 모드와 모바일 화면 지원
- **API** (`/api/docs`에서 확인): `POST /api/ask`, `GET /api/search`, `GET /api/feed`, `GET·POST·DELETE /api/watchlist`, `GET /api/diff`, `GET /api/companies`, `GET /api/compare`, `GET /api/company/{종목코드}/report.pdf`. 임베딩 모델은 첫 질문 때 한 번만 올리고, DB 연결은 요청마다 따로 엶

- **회사 대시보드** (웹 "회사" 탭, `GET /api/company/{종목코드}`): 최근 연도 핵심 지표와 전년 대비 변화, 연도별 매출·이익 막대 차트, 이익률 꺾은선 차트(표로 보기 포함), 최근 90일 공시, 최근 사업보고서 변경점 요약. 숫자는 질문 답변과 같은 계정 선택 규칙을 씀
- **로그인** (`web/auth.py`, `AUTH_REQUIRED=true`일 때만): 가입·로그인·로그아웃·비밀번호 변경, 사용자별 관심 종목, 로그인 시도 제한, 다른 사이트에서 보낸 요청 차단, 보안 헤더(CSP 등). 관리 명령 `dartrag user add/password/list/remove`
- **대화** (`answer/conversation.py`, `web/chat.py`): "그럼 전년은?", "SK하이닉스는?"처럼 이어지는 질문의 회사·연도·주제를 앞 질문에서 이어받아 완전한 질문으로 바꾸고, 어떻게 해석했는지 응답에 함께 돌려줌. 질문 속 회사로 검색 범위를 좁혀 다른 회사 문서가 섞이지 않게 함. 대화 기록 저장·조회·삭제(로그인하면 사용자별). `POST /api/ask/stream`은 답변을 만들어지는 대로 보내는 스트리밍(Server-Sent Events)
- **답변 평가** (`POST /api/messages/{id}/feedback`): 👍/👎와 사유(숫자 틀림, 출처 틀림, 못 찾음 등). `dartrag feedback stats`로 집계, `dartrag feedback export`로 👎 질문을 평가셋 후보로 내보냄
- **검색 품질** (`search/hybrid.py`, `search/rerank.py`):
  - 비교 질문처럼 회사가 여럿이면 회사마다 따로 찾아 번갈아 섞습니다. 한 회사가 근거를 독차지하지 않게 하려는 것입니다.
  - 해마다 똑같이 반복되는 문단은 가장 최근 것 하나만 남깁니다.
  - 리랭커(bge-reranker-v2-m3)로 후보 30개의 순서를 다시 매깁니다. `.env`의 `RERANK_MODEL`을 비우면 끌 수 있습니다.
  - 관련도가 같으면 최신 보고서를 앞에 둡니다.
  - 답변 근거에는 같은 섹션의 앞뒤 문단을 붙여 맥락을 넓힙니다.
- **거절과 인젝션 방어** (`answer/guard.py`, `answer/prompt.py`):
  - 매수·매도 추천, 주가 전망, 프롬프트 인젝션 요청은 LLM에 보내지 않고 정해진 안내로 답합니다.
  - 근거 원문은 `<source>` 태그 안의 자료로만 다루도록 프롬프트에 명시합니다.
  - 평가셋에 적대적 문항 10개를 추가했습니다.
- **답변 캐시** (`answer/cache.py`, Redis와 Qdrant):
  - 같은 질문은 Redis에서, 뜻이 거의 같은 질문은 질문 벡터로 찾아 바로 답합니다.
  - 숫자(연도 등)가 하나라도 다르면 캐시를 쓰지 않습니다. 회사가 정해지지 않은 질문은 뜻이 비슷한 질문을 찾는 캐시를 쓰지 않습니다.
  - 새 데이터를 색인하거나 프롬프트를 바꾸면 이전 캐시는 자동으로 무효가 됩니다.
- **분기 실적과 기업 비교** (`finance/series.py`, `web/insights.py`):
  - `GET /api/company/{종목코드}/quarters`: 분기별 매출·영업이익·순이익. 4분기는 따로 공시되지 않아 연간 금액에서 3분기 누적 금액을 빼서 계산하고 `derived`로 표시합니다.
  - `GET /api/compare?stocks=005930&stocks=000660`: 2~5개 회사의 공통 최신 연도 지표와 연도별 추이.
  - `POST /api/compare/summary`: 회사들의 공시 본문을 근거로 사업 구조(`business`), 위험 요인(`risk`), 투자 계획(`investment`)을 비교 설명합니다.
- **PDF 리포트** (`report/`, `dartrag report -s 005930`, 웹 회사 탭의 "PDF 리포트"):
  - 연간 재무 추이 표와 차트, 분기 실적, 사업 개요와 위험 요인 요약(근거 링크 포함), 직전 사업보고서 대비 변경점, 최근 주요 공시, 출처를 담습니다.
  - 요약은 답변 모델을 쓰므로 오래 걸립니다. 웹에서는 기본으로 빼고(`?llm=true`로 포함), 명령에서는 기본으로 넣습니다(`--no-llm`으로 뺌).
  - 한글 글꼴은 나눔고딕, 애플고딕, 맑은 고딕 순으로 찾아 PDF에 넣습니다. 다른 글꼴을 쓰려면 `.env`의 `REPORT_FONT`에 TTF 경로를 적습니다.
- **변경점 요약** (`changes/summary.py`, `dartrag diff -s 005930 --summary`, `GET /api/diff/summary`, 웹 "변경점" 탭):
  - 코드가 찾은 추가·삭제·수정 문장에 번호를 붙여 답변 모델에 주고, "새로 생긴 위험", "빠진 내용", "주요 변경"으로 정리하게 합니다.
  - 요약 문장마다 근거 번호를 달게 하고, 근거 번호가 없거나 없는 번호를 단 문장은 버립니다. 근거에 없는 숫자가 나오면 "숫자 확인 필요"로 표시합니다.
  - 매출·이익 같은 숫자 변화는 모델이 아니라 재무 데이터로 계산합니다.
  - 한 번 만든 요약은 DB에 저장해 다시 씁니다. 모델이나 요약 규칙이 바뀌면 새로 만듭니다.
- **알림 채널** (`feed/channels.py`, `feed/alerts.py`):
  - 혼자 쓸 때: `.env`의 `ALERT_WEBHOOK_URL`(Slack·Discord), `ALERT_TELEGRAM_CHAT_ID`, `ALERT_EMAIL_TO`로 받습니다. `dartrag alert test`로 시험 메시지를 보낼 수 있습니다.
  - 로그인을 켰을 때: 사용자마다 웹 "관심 종목" 탭에서 이메일(인증 메일의 링크를 열어야 시작)과 텔레그램(봇 연결 링크)을 등록합니다. 한 번에 모인 공시는 사용자·채널마다 한 통으로 묶어 보내고, 메일에는 "그만 받기" 링크를 넣습니다.
  - 정기보고서가 올라오면 변경점 요약의 핵심 문장을 알림에 함께 넣습니다. 이를 위해 공시 피드가 정기공시도 받습니다.
  - 텔레그램 봇 연결은 공개 서버에서는 웹훅(`dartrag telegram webhook`), 내 컴퓨터에서는 `dartrag telegram poll`로 받습니다.
- **백그라운드 작업자** (`worker/`, Celery와 Redis):
  - 공시 피드를 10분마다 받고, 새 정기보고서는 5분마다 원문·재무 수집 → 파싱 → 색인 → 변경점 요약 → 데이터 검증 → 알림까지 이어서 처리합니다. 공시 후 30분 안에 검색과 알림에 반영하는 것이 목표입니다.
  - 정기보고서 알림은 변경점 요약을 붙이려고 처리가 끝날 때까지(최대 2시간) 기다립니다.
  - `BACKFILL_ENABLED=true`면 전체 상장사의 2015년 이후 정기보고서를 채웁니다. OpenDART 하루 한도(키당 20,000회)를 여러 작업자가 Redis로 함께 세고, 새 공시 처리 몫(`DART_RESERVE`)은 남겨 둡니다. 회사 단위로 진행 상황을 저장해 재시작해도 이어서 합니다.
  - 새벽에 재무 데이터 검증과 보관 기간 정리(대화 180일, 만료 세션, 오래된 기록)를 합니다.
  - 실행: 터미널 두 개에서 `celery -A dartrag.worker.celery_app worker -Q dart,process,default -c 2`와 `celery -A dartrag.worker.celery_app beat`. 상태는 `dartrag jobs status`, 작업 하나만 바로 돌리려면 `dartrag jobs run ingest`.
- **재무 데이터 검증** (`finance/validate.py`, `dartrag validate`): 자산총계 = 부채총계 + 자본총계(0.5% 넘게 어긋나면 오류), 음수 자산·매출, 단위 이상, 사업보고서의 핵심 계정 누락, 매출 4배 이상 급변을 찾습니다. 회사 대시보드에 "데이터 확인 필요"로 표시합니다.
- **준비 점검과 일괄 실행** (`dartrag doctor`, `dartrag run`): 위 "한 번에 실행하기" 참고. 인증키는 화면에 출력하지 않음

- **새 웹 화면** (`frontend/`, Next.js와 Tailwind):
  - 질문하기(답변이 만들어지는 대로 표시, 대화 기록), 회사 대시보드(분기 실적 포함), 기업 비교, 공시 피드, 관심 종목과 알림 설정, 변경점, 로그인·가입·계정, 이용약관과 개인정보처리방침 화면이 있습니다.
  - 마우스를 올리면 값이 뜨는 차트와 "표로 보기", 다크 모드, 모바일 화면을 지원합니다.
  - 개발할 때: `dartrag serve`를 켠 상태에서 `cd frontend`, `npm install`, `npm run dev` 순서로 실행하고 http://localhost:3000 을 엽니다. `/api` 요청은 `API_ORIGIN`(기본 http://127.0.0.1:8000)으로 넘깁니다. 이때 `.env`에 `ALLOWED_ORIGINS=http://localhost:3000`을 넣어야 저장·질문 요청이 막히지 않습니다.
  - 배포용 빌드는 `npm run build` 후 `npm start`입니다. 기존 정적 화면(`dartrag serve`의 http://127.0.0.1:8000)도 그대로 씁니다.

- **운영 관측과 품질 관리** (`obs/`, `eval/gate.py`, `infra/monitoring/`):
  - 지표: API 서버의 `/metrics`에 요청 수·응답 시간, 답변 결과(답함·못 찾음·거절·캐시·실패), 검색·첫 글자·생성 단계별 시간, 토큰 수, 👍/👎, 요청 한도 초과, 작업자 상태, 처리 대기, 데이터 검증 오류, OpenDART 사용량, 최근 평가 결과를 내보냅니다.
  - 대시보드: `docker compose -f infra/docker-compose.yml --profile monitoring up -d` 후 http://localhost:3002 (Grafana, 처음 계정 admin/admin). 경보 규칙 11개(API 멈춤, 답변 실패 급증, 피드 1시간 이상 멈춤, OpenDART 한도 90% 등)가 들어 있습니다.
  - LLM 추적: `--profile langfuse`로 Langfuse를 띄우고 `.env`에 `LANGFUSE_HOST=http://localhost:3001`을 넣으면 질문마다 검색 결과, 모델에 보낸 내용, 답변, 토큰, 첫 글자까지 걸린 시간이 http://localhost:3001 에 남습니다(처음 계정 admin@dartrag.local / dartrag-admin). 메모리를 2~3GB 더 씁니다. 사용자·대화 번호는 `SECRET_KEY`로 만든 가명으로만 보내고, 탈퇴하면 그 가명의 추적 삭제를 요청합니다. 보관 기간 30일은 Langfuse 프로젝트 설정에서 정합니다([docs/deploy.md](docs/deploy.md) 11절).
  - 오류 수집: `.env`에 `SENTRY_DSN`을 넣으면 API와 작업자의 오류가 Sentry로 갑니다. 질문 내용, 이메일, 쿠키, 지역 변수는 보내기 전에 지웁니다.
  - 요청 한도: 질문은 사용자마다 1분 6번·하루 200번, 비교 설명·변경점 요약 새로 만들기·요약 PDF는 1시간 10번이 기본입니다. 넘으면 429와 다시 시도할 시간을 돌려줍니다.
  - 배포 기준: `eval/release_criteria.toml`에 통과율·검색 적중률·출처 표시율·숫자 확인 실패율·응답 시간 기준과 기준선 대비 하락 허용폭이 있습니다. `dartrag eval run ... --gate`가 기준 미달이면 실패하고, 결과를 `dartrag eval baseline`으로 `eval/baseline.json`에 저장해 올리면 CI가 그 기준선과 프롬프트 버전을 검사합니다.
  - 정기 평가: `EVAL_SCHEDULE_ENABLED=true`면 일요일 새벽에 평가 문항 일부로 품질을 재고 대시보드에 보여 줍니다.
  - 👎 받은 질문: `dartrag feedback export`로 후보에 모으고, 정답을 채운 뒤 `dartrag eval promote`로 평가셋에 옮깁니다.

## 알려진 제한
- 결산월이 12월이 아닌 회사는 재무제표 API의 사업연도(`bsns_year`) 기준을 실제 응답으로 검증하기 전까지 재무 수집을 건너뜁니다 (원문은 수집). 결산월 정보도 아직 기본값 12로 저장됩니다.
- 계정과목 표준화(회사별 계정명 → 표준 코드)는 1~2단계에서 추가합니다.

## 개발
```bash
make test     # 단위 테스트 (DB 통합 테스트는 TEST_DATABASE_URL 이 있을 때만)
make lint
```

브라우저 종단 테스트(Playwright)는 실제 API 서버(가짜 검색·언어 모델)와 테스트용 Postgres, `next start`를 띄워 가입 → 로그인 → 질문 → 내 데이터 내려받기 → 탈퇴와 로그인 뒤 돌아갈 주소 검사를 Chromium으로 확인합니다. CI의 `e2e` 작업이 같은 것을 돌립니다.
```bash
cd frontend
npx playwright install chromium   # 처음 한 번
API_ORIGIN=http://127.0.0.1:8765 npm run build
E2E_DATABASE_URL=postgresql://dartrag:dartrag@localhost:5432/dartrag_e2e npm run e2e
```
