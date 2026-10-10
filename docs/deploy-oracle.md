# Oracle Cloud 무료 서버에 공개하기

포트폴리오 링크처럼 **언제 열어도 접속되는 주소**를 돈 들이지 않고 만드는 방법입니다. 위에서부터 한 줄씩 따라 하면 됩니다. 명령은 한 줄에 하나씩 붙여 넣으세요.

일반 서버에 배포하는 전체 설명은 [deploy.md](deploy.md)에 있고, 이 문서는 그중 Oracle 무료 서버에 맞게 달라지는 부분을 순서대로 정리한 것입니다.

## 미리 알아 둘 것

- **무료 범위**: Ampere A1(ARM) 서버를 **2코어·메모리 12GB**까지 무료로 씁니다. 2026년 6월 15일부터 예전(4코어·24GB)의 절반으로 줄었습니다. 디스크는 200GB까지 무료입니다. ([Oracle 문서](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm))
- **가입할 때 카드 등록**이 필요합니다. 본인 확인용이며, 무료 범위 안에서는 요금이 나가지 않습니다. 계정을 유료(Pay As You Go)로 바꾸지 마세요. 무료 범위를 넘는 만큼 요금이 나갈 수 있습니다.
- **리전은 서울(South Korea Central)** 을 고릅니다. 춘천 리전에서는 무료 ARM 서버를 만들 수 없고, 처음 고른 리전(홈 리전)은 나중에 바꿀 수 없습니다.
- **12GB에 맞춘 구성**: 맥과 달리 서버에서는 답변 모델을 qwen3:4b로 씁니다. 검색 리랭커는 켜 둡니다. 공시를 처음 많이 받는 일은 맥에서 하고, 결과만 서버로 옮깁니다. 이후 새로 나오는 공시는 서버가 직접 받고, 보고서 내용까지 색인하는 회사는 기본 15개사와 직접 더한 회사로 줄여 무료 범위 안에 둡니다 (11단계).
- **속도**: 그래픽카드가 없어서 답변이 맥보다 느립니다. 화면의 예시 질문은 답을 미리 만들어 두어 바로 나옵니다. 실제 속도는 서버를 띄운 뒤 `dartrag bench`로 잽니다.
- **유휴 서버 회수**: 7일 동안 CPU·네트워크·메모리 사용률이 모두 20% 미만이면 Oracle이 서버를 회수할 수 있습니다. 이 구성은 검색 모델을 메모리에 올려 두므로 메모리 사용률이 20%를 넘습니다.

## 1. Oracle 가입

1. https://signup.cloud.oracle.com 에서 가입합니다.
2. **Home Region**에서 `South Korea Central (Seoul)`을 고릅니다.
3. 카드 정보를 넣고 가입을 마칩니다. 계정이 준비되기까지 몇 분 걸릴 수 있습니다.

## 2. 접속용 키 만들기 (맥)

맥의 터미널에서 실행합니다. 물어보는 질문은 모두 그냥 `Enter`를 누르면 됩니다.

```bash
ssh-keygen -t ed25519 -f ~/.ssh/oracle_dartrag
```

아래 명령이 보여 주는 한 줄(`ssh-ed25519`로 시작)을 3단계에서 붙여 넣습니다.

```bash
cat ~/.ssh/oracle_dartrag.pub
```

`~/.ssh/oracle_dartrag` (끝에 `.pub`이 없는 파일)는 비밀 키입니다. 다른 사람에게 보내거나 채팅에 붙여 넣지 마세요.

## 3. 서버 만들기

Oracle 콘솔에서 **Compute → Instances → Create instance**로 갑니다.

- **Name**: `dartrag`
- **Image and shape**
  - Image: **Change image → Ubuntu → Canonical Ubuntu 24.04**
  - Shape: **Change shape → Ampere → VM.Standard.A1.Flex**, OCPU **2**, Memory **12GB**
- **Networking**: 새 가상 클라우드 네트워크(VCN)와 공용 서브넷을 만들고, **Assign a public IPv4 address**를 켭니다.
- **Add SSH keys**: **Paste public keys**를 고르고 2단계에서 복사한 한 줄을 붙여 넣습니다.
- **Boot volume**: **Specify a custom boot volume size**를 켜고 `100` GB로 둡니다.

**Create**를 누르고, 상태가 `RUNNING`이 되면 화면의 **Public IP address**를 적어 둡니다.

"Out of capacity" 오류가 나면 무료 서버 자리가 잠시 없는 것입니다. 몇 시간 뒤나 다음 날 다시 만들어 보세요.

## 4. 웹 포트 열기 (콘솔)

바깥에서 80·443 포트로 들어올 수 있게 합니다.

1. 인스턴스 화면의 **Primary VNIC → Subnet** 링크를 누릅니다.
2. **Security Lists → Default Security List**로 들어가 **Add Ingress Rules**를 누릅니다.
3. 아래 두 규칙을 추가합니다.
   - Source CIDR `0.0.0.0/0`, IP Protocol **TCP**, Destination Port Range `80,443`
   - Source CIDR `0.0.0.0/0`, IP Protocol **UDP**, Destination Port Range `443`

## 5. 서버 접속과 기본 설정

맥에서 접속합니다. `서버IP`는 3단계에서 적어 둔 주소로 바꿉니다. 처음 접속할 때 묻는 질문에는 `yes`를 입력합니다.

```bash
ssh -i ~/.ssh/oracle_dartrag ubuntu@서버IP
```

이제부터는 서버 터미널입니다.

**서버 안 방화벽 열기.** Oracle의 Ubuntu 이미지는 서버 안에서도 22번 포트 말고는 막아 둡니다. ufw는 쓰지 말고 아래처럼 엽니다. Docker를 설치하기 **전에** 해야 합니다.

```bash
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT
```

```bash
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT
```

```bash
sudo iptables -I INPUT 6 -m state --state NEW -p udp --dport 443 -j ACCEPT
```

```bash
sudo netfilter-persistent save
```

확인합니다. 80·443 줄이 `REJECT` 줄보다 위에 있으면 됩니다.

```bash
sudo iptables -L INPUT --line-numbers
```

**스왑 8GB 만들기.** 질문과 새 공시 색인이 겹쳐 메모리가 잠깐 모자랄 때 서비스가 죽지 않게 받쳐 줍니다.

```bash
sudo fallocate -l 8G /swapfile
```

```bash
sudo chmod 600 /swapfile
```

```bash
sudo mkswap /swapfile
```

```bash
sudo swapon /swapfile
```

```bash
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

**Docker 설치.**

```bash
curl -fsSL https://get.docker.com -o get-docker.sh
```

```bash
sudo sh get-docker.sh
```

```bash
sudo usermod -aG docker $USER
```

`exit`로 나갔다가 5단계 첫 명령으로 다시 접속합니다. 아래 명령이 버전을 보여 주면 됩니다.

```bash
docker compose version
```

## 6. 무료 주소 만들기 (DuckDNS)

https 인증서를 받으려면 도메인이 필요합니다. DuckDNS에서 `내이름.duckdns.org` 같은 주소를 무료로 받습니다.

1. https://www.duckdns.org 에서 GitHub나 Google 계정으로 로그인합니다.
2. **sub domain**에 원하는 이름(예: `dartrag-yurim`)을 넣고 **add domain**을 누릅니다.
3. 그 줄의 **current ip**에 3단계의 서버 IP를 넣고 **update ip**를 누릅니다.

이제 `dartrag-yurim.duckdns.org`가 서버를 가리킵니다. 아래에서 `내주소`는 이 주소로 바꿔 씁니다.

## 7. 코드 받기와 설정

```bash
sudo mkdir -p /opt/dartrag
```

```bash
sudo chown $USER /opt/dartrag
```

```bash
git clone https://github.com/leeyourimm/LLM_Project1.git /opt/dartrag
```

```bash
cd /opt/dartrag/infra/prod
```

```bash
cp .env.example .env
```

작은 서버용 설정 세 줄을 붙입니다. 앞의 두 줄이 있으면 `docker compose` 명령이 12GB용 메모리 설정(`docker-compose.small.yml`)과 Ollama 컨테이너를 함께 씁니다. 세 번째 줄은 새 보고서를 색인할 회사를 줄여 디스크와 메모리를 아낍니다 (11단계).

```bash
echo 'COMPOSE_FILE=docker-compose.yml:docker-compose.small.yml' >> .env
```

```bash
echo 'COMPOSE_PROFILES=ollama' >> .env
```

```bash
echo 'INDEX_SCOPE=focus' >> .env
```

비밀값을 만듭니다. 아래 명령을 세 번 실행해 나온 값을 각각 `SECRET_KEY`, `POSTGRES_PASSWORD`, `GRAFANA_ADMIN_PASSWORD`에 씁니다.

```bash
openssl rand -hex 32
```

`.env`를 엽니다.

```bash
nano .env
```

바꿀 것:

- `DOMAIN=내주소`
- `PUBLIC_URL=https://내주소`
- `SECRET_KEY`, `POSTGRES_PASSWORD`, `GRAFANA_ADMIN_PASSWORD`: 위에서 만든 값
- `DART_API_KEY`: OpenDART 인증키 (새 공시를 서버가 직접 받을 때 씀)
- `OLLAMA_URL=http://ollama:11434`
- `LLM_MODEL=qwen3:4b`
- `ALLOW_GUEST=true`: 방문자가 가입 없이 바로 질문해 볼 수 있게 합니다. 체험 계정은 24시간 뒤 기록과 함께 지워지고, 알림과 계정 보안 설정은 쓸 수 없습니다. 접속 주소마다 하루 질문 20개까지입니다. `AUTH_REQUIRED=true`는 그대로 둡니다.

`RERANK_MODEL`은 그대로 둡니다. 저장은 `Ctrl+O`, `Enter`, 나가기는 `Ctrl+X`입니다.

```bash
chmod 600 .env
```

## 8. 처음 실행

```bash
docker compose up -d --build
```

ARM 2코어에서 이미지를 처음 만드느라 오래 걸립니다. 끝나면 답변 모델을 받습니다 (약 2.5GB).

```bash
docker compose exec ollama ollama pull qwen3:4b
```

상태를 봅니다. beat, caddy를 뺀 모든 줄이 `(healthy)`면 됩니다.

```bash
docker compose ps
```

브라우저에서 `https://내주소`를 엽니다. 데이터를 옮기기 전이므로 질문하면 "아직 수집·색인한 공시가 없어 답할 수 없습니다"가 나오면 정상입니다.

## 9. 맥의 데이터를 서버로 옮기기

공시 수집·파싱·색인은 맥에서 `dartrag run`으로 끝낸 뒤에 합니다.

**맥에서** 내보냅니다. 맥의 Docker 서비스(`make up`, `make up-search`)가 켜져 있어야 합니다.

```bash
cd ~/LLM_Project1
```

```bash
infra/prod/export_local.sh
```

마지막 줄에 나온 폴더를 서버로 올립니다. `폴더경로`는 그 줄의 경로로 바꿉니다.

```bash
scp -i ~/.ssh/oracle_dartrag -r 폴더경로 ubuntu@서버IP:/opt/dartrag/infra/prod/backups/
```

**서버에서** 되돌립니다. `폴더이름`은 올린 폴더의 이름(날짜-시각)입니다. `yes`를 입력해야 진행됩니다.

```bash
/opt/dartrag/infra/prod/restore.sh /opt/dartrag/infra/prod/backups/폴더이름
```

키워드 검색 색인은 옮기지 않았으므로 DB의 문단으로 다시 채웁니다. 임베딩은 다시 계산하지 않아 금방 끝납니다.

```bash
docker compose exec api dartrag index --keyword-only
```

예시 질문의 답을 미리 만들어 둡니다. 방문자가 예시를 누르면 기다리지 않고 바로 답이 나옵니다. 데이터가 바뀌면 작업자가 30분마다 확인해 다시 만듭니다.

```bash
docker compose exec api dartrag cache warm
```

맥의 데이터를 다시 옮기면 서버의 계정과 대화 기록도 맥의 것으로 덮어써집니다. 관리자 계정은 옮긴 뒤에 만듭니다.

```bash
docker compose exec api dartrag user add 내이메일@example.com
```

## 10. 확인

- 브라우저에서 `https://내주소`를 열고 예시 질문을 눌러 봅니다.
- 메모리 사용량을 봅니다. `MEM USAGE`가 `LIMIT`에 계속 붙어 있는 컨테이너가 있으면 알려 주세요.

```bash
docker stats --no-stream
```

```bash
free -h
```

## 11. 데이터가 쌓여도 무료로 유지하기

서버는 새 공시를 몇 년이고 계속 받습니다. 무엇을 쌓는지, 얼마나 늘어나는지, 어떻게 확인하는지 정리합니다.

**색인하는 회사.** 7단계의 `INDEX_SCOPE=focus` 덕분에 새 사업·반기·분기보고서의 원문을 받아 질문용 검색 색인까지 만드는 회사는 **기본 15개사**(삼성전자, SK하이닉스, 현대차 등)와 **관리자가 직접 더한 회사**뿐입니다. 회원이 관심 종목에 넣은 회사라도 저절로 더해지지는 않습니다.

**색인하지 않는 회사.** 나머지 상장사도 공시 피드와 관심 종목 알림에는 그대로 나옵니다. 공시 제목 같은 작은 정보만 저장하므로 공간을 거의 쓰지 않습니다. 다만 그 회사의 보고서 내용으로 질문에 답하거나 재무 차트를 그리지는 않습니다. 필요한 회사는 아래처럼 더합니다.

**회사 더하기.** 그 회사의 최근 3개 사업연도와 올해 정기보고서를 바로 받아 색인하고(몇 분에서 수십 분), 이후 나오는 보고서도 자동으로 받습니다. 검색 모델을 한 번 더 메모리에 올리므로, 질문에 답하고 있는 api 컨테이너 안이 아니라 잠깐 따로 띄운 컨테이너(`run --rm`)에서 실행합니다. `066570`(LG전자)은 예시이니 원하는 종목코드로 바꿉니다.

```bash
docker compose run --rm api dartrag scope add 066570
```

OpenDART에 연결하지 못하거나 오늘 호출 한도를 다 썼다는 안내가 나와도 회사는 목록에 남고, 이후 새 보고서부터는 작업자가 받습니다. 지난 보고서는 나중에 같은 명령을 다시 실행해 받습니다.

**목록 보기.**

```bash
docker compose exec api dartrag scope list
```

**회사 빼기.** 앞으로 나오는 보고서를 받지 않습니다. 이미 색인한 내용은 지우지 않아 검색에 그대로 남습니다. 기본 15개사는 뺄 수 없습니다.

```bash
docker compose exec api dartrag scope remove 066570
```

**검색 벡터는 디스크에.** 검색용 벡터(Qdrant)는 원본을 디스크에 두고, 4분의 1 크기로 줄인 사본만 메모리에 둡니다. 검색할 때는 사본으로 후보를 고른 뒤 원본으로 다시 점수를 매기므로 검색 품질은 거의 같습니다. 9단계에서 맥의 예전 방식 데이터를 옮겨 왔어도, 작업자가 15분마다 하는 색인 확인 때 그 자리에서 이 방식으로 바꿉니다.

**얼마나 늘어나나요? (추정)** 아래 숫자는 모두 추정입니다. 실제 값은 바로 다음의 명령으로 확인합니다.

- 회사 하나는 1년에 정기보고서를 약 4건(사업 1, 반기 1, 분기 2) 냅니다. 가끔 정정 보고서가 더해집니다.
- 대형주 보고서 한 건은 본문과 표를 합쳐 약 30만~50만 자로 잡았습니다. 이를 1,500자 이하 조각(청크)으로 나누므로(`src/dartrag/parsing/chunking.py`) 보고서 하나에 청크가 약 300~500개입니다. 저장소의 시험용 보고서(`tests/fixtures`)는 아주 짧은 견본이라 근거로 쓰지 않았습니다.
- 청크 하나는 디스크를 약 15KB 씁니다: 검색 벡터 원본 4KB와 색인 약 1KB(Qdrant), 문단 원문 약 4KB(Postgres), 키워드 색인 약 5KB(OpenSearch). 보고서 원문 파일은 한 건에 약 1MB입니다.
- 메모리에 두는 벡터 사본은 청크 하나에 약 1KB입니다. 예전 방식(원본을 메모리에)이면 4KB입니다.

| 색인 범위 | 1년에 늘어나는 보고서 | 디스크 (1년) | Qdrant 메모리 (1년) |
|---|---|---|---|
| 기본 15개사 | 약 60건 (청크 약 2만~3만 개) | 약 0.3~0.5GB | 약 20~30MB |
| 회사 하나 더할 때마다 | 약 4건 | 약 20~35MB | 약 1~2MB |
| 모든 상장사 (`INDEX_SCOPE=all`) | 약 1만 건 (작은 회사는 보고서가 짧아 청크 약 100만~200만 개) | 약 20~40GB | 약 1~2GB |

기본 15개사만 색인하면 10년이 지나도 디스크는 약 5GB, Qdrant 메모리는 약 0.3GB 늘어납니다. 디스크(100GB, 무료는 200GB까지)와 Qdrant 메모리 한도(1GB, `docker-compose.small.yml`) 안에 넉넉히 들어갑니다. 맥에서 처음 옮겨 온 데이터(15개사, 4개 사업연도)는 이 표의 약 4년 치입니다. 모든 상장사를 색인하면 Qdrant 메모리가 1년 안에 한도를 넘고, 디스크도 몇 년 안에 찹니다.

**확인하기.** 한 달에 한 번쯤 봅니다. 디스크는 맨 끝이 `/`인 줄의 `Use%`가 80%를 넘지 않으면 됩니다.

```bash
df -h
```

컨테이너별 메모리입니다. `qdrant` 줄의 `MEM USAGE`가 `LIMIT`(1GiB)에 계속 붙어 있으면 알려 주세요.

```bash
docker stats --no-stream
```

## 문제 해결

- **"Out of capacity"로 서버가 안 만들어짐**: 무료 서버 자리가 잠시 없는 것입니다. 시간을 두고 다시 시도합니다.
- **https 인증서가 발급되지 않음**: `docker compose logs caddy`를 봅니다. DuckDNS의 IP가 서버 IP와 같은지, 4단계(콘솔)와 5단계(서버 안 방화벽)에서 80·443을 모두 열었는지 확인합니다.
- **서버에 접속이 안 됨 (ssh)**: 5단계에서 22번 줄을 지우지 않았는지 확인합니다. 콘솔의 **Console connection**으로 들어가 고칠 수 있습니다.
- **컨테이너가 계속 다시 시작됨**: `docker compose logs --tail 100 컨테이너이름`을 봅니다. 메모리 한도 때문이면 `docker stats` 결과와 함께 알려 주세요. `docker-compose.small.yml`의 한도를 조정합니다.
- **답변이 "모델에 연결할 수 없음"**: `.env`의 `OLLAMA_URL=http://ollama:11434`인지, `docker compose exec ollama ollama list`에 qwen3:4b가 있는지 봅니다.

업데이트, 백업, 모니터링은 [deploy.md](deploy.md)의 같은 이름 절을 따르면 됩니다. `.env`에 `COMPOSE_FILE`이 들어 있어서 그 문서의 `docker compose` 명령을 그대로 써도 12GB 설정이 함께 적용됩니다.
