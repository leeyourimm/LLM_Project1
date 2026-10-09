# AWS 서버 한 대에 배포하기 (Terraform)

`infra/terraform/`은 AWS 서울 리전에 이 서비스를 돌릴 서버 한 대와 주변 리소스를 만듭니다.
명령은 모두 한 줄에 하나씩 적었습니다. 그대로 한 줄씩 복사해서 실행하세요.

## 무엇이 만들어지나
| 리소스 | 설명 |
|---|---|
| EC2 서버 | Ubuntu 24.04 LTS, 기본 `m7i.xlarge`(4 vCPU, 메모리 16GB). qwen3:8b와 bge-m3를 함께 돌리려면 16GB 이상이 필요합니다. |
| 디스크 2개 | 루트 30GB(OS), 데이터 100GB(`/srv/dartrag`, Docker 볼륨·Ollama 모델·코드). 둘 다 gp3, 암호화. |
| 고정 IP | Elastic IP. 서버를 껐다 켜도 주소가 바뀌지 않습니다. |
| 보안 그룹 | 80/443만 전체 공개. SSH(22)는 기본으로 닫혀 있고, 지정한 IP만 열 수 있습니다. |
| IAM 역할 | SSM Session Manager 접속 권한, 백업 버킷 읽기/쓰기(삭제 권한 없음). |
| S3 백업 버킷 | 버전 관리, 암호화, 공개 차단, HTTPS 강제. 30일 안에 이전 버전까지 자동 삭제(개인정보 처리방침 약속). |
| 선택: Route53 | `domain_name`과 `hosted_zone_id`를 채우면 A 레코드를 만듭니다. |
| 선택: 경보 메일 | `alarm_email`을 채우면 서버 상태 검사가 실패할 때 메일을 보냅니다. |

네트워크는 계정에 원래 있는 **기본 VPC**를 그대로 씁니다. 서버가 한 대이고 인터넷에서 바로 접속받기 때문에
사설 서브넷이나 NAT 게이트웨이(월 수만 원)를 따로 만들 이유가 없고, 만드는 리소스가 적을수록 실수할 곳도 적습니다.
접근 통제는 보안 그룹이 맡습니다.

서버가 처음 켜질 때 cloud-init이 자동으로 다음을 합니다.
1. 데이터 디스크를 포맷(처음 한 번만)하고 `/srv/dartrag`에 연결
2. Docker Engine과 compose 플러그인 설치(Docker 데이터는 `/srv/dartrag/docker`에 저장)
3. Ollama 설치, `qwen3:8b` 내려받기(모델은 `/srv/dartrag/ollama`에 저장)
4. 저장소를 `/srv/dartrag/app`에 clone

비밀값(OpenDART 키, DB 비밀번호 등)은 Terraform에도 cloud-init에도 넣지 않습니다. 서버에 접속해서 직접 `infra/prod/.env`에 입력합니다.

## 1. 준비물 설치 (내 컴퓨터, macOS 기준)
Homebrew가 있다고 가정합니다. Windows라면 WSL(Ubuntu)에서 [Terraform 설치 안내](https://developer.hashicorp.com/terraform/install)를 따르세요.
```zsh
brew tap hashicorp/tap
brew install hashicorp/tap/terraform
brew install awscli
brew install --cask session-manager-plugin
```
설치 확인:
```zsh
terraform version
aws --version
session-manager-plugin --version
```

## 2. AWS 자격 증명 설정
**액세스 키나 비밀 키를 채팅, 이슈, 코드, 이 저장소의 어떤 파일에도 붙여 넣지 마세요.** 키는 내 컴퓨터의 AWS CLI 설정에만 둡니다.

회사나 조직 계정이라 IAM Identity Center(SSO)를 쓴다면 이 방법을 권장합니다. 질문에 답하면 브라우저로 로그인합니다.
```zsh
aws configure sso
```
개인 계정에서 IAM 사용자 액세스 키를 쓴다면(루트 계정 키는 만들지 마세요):
```zsh
aws configure
```
기본 리전을 물으면 `ap-northeast-2`를 입력합니다. 프로필 이름을 따로 정했다면 터미널에서 먼저 지정합니다.
```zsh
export AWS_PROFILE=내프로필이름
```
제대로 연결됐는지 확인합니다. 계정 번호가 나오면 성공입니다.
```zsh
aws sts get-caller-identity
```

## 3. 변수 파일 만들기
저장소 최상위 폴더에서 시작합니다.
```zsh
cd infra/terraform
cp terraform.tfvars.example terraform.tfvars
```
`terraform.tfvars`를 편집기로 열어 필요한 값만 고칩니다. 이 파일은 `.gitignore`에 있어 git에 올라가지 않습니다.
- `instance_type`: 기본 `m7i.xlarge`. 비용을 줄이려면 `r7i.large`(메모리는 같고 CPU가 절반이라 답변이 느림).
- `git_ref`: 서버에 받을 브랜치(기본 `main`).
- `alarm_email`: 장애 알림 메일(선택). 적용 후 받은 메일에서 구독 확인을 눌러야 합니다.
- `domain_name`, `hosted_zone_id`: Route53에 도메인이 있을 때만(선택).
- `ssh_allowed_cidrs`: 비워 두세요. 접속은 SSM으로 합니다.

## 4. 만들기: init, plan, apply
```zsh
terraform init
terraform plan
terraform apply
```
- `init`: 필요한 플러그인을 내려받습니다. 처음 한 번, 그리고 버전을 바꿨을 때 실행합니다.
- `plan`: 무엇을 만들지 미리 보여 줍니다. 아무것도 만들지 않습니다.
- `apply`: 다시 한 번 계획을 보여 주고 `yes`를 입력하면 실제로 만듭니다. 2~3분 걸립니다.

끝나면 결과 값이 나옵니다. 나중에 다시 보려면:
```zsh
terraform output
```
서버 안의 설치 작업(Docker, Ollama, 모델 약 5GB 다운로드)은 apply가 끝난 뒤에도 10~20분 더 걸립니다.

## 5. 한 달 예상 비용
가정: 서울 리전 온디맨드 요금, 한 달 730시간 내내 켜 둠, 환율 1달러 = 1,400원, 백업 몇 GB, 인터넷으로 나가는 트래픽 월 100GB 이하(무료 구간), 부가세 10% 별도. 요금은 바뀔 수 있으니 실제 금액은 [AWS 요금 계산기](https://calculator.aws/)로 확인하세요.

| 항목 | 계산 | 월 USD | 월 원화(대략) |
|---|---|---|---|
| EC2 m7i.xlarge | 약 $0.248/시간 × 730 | 약 $181 | 약 25만 3천 원 |
| EBS gp3 130GB | $0.0912/GB × 130 | 약 $12 | 약 1만 7천 원 |
| 공인 IPv4(Elastic IP) | $0.005/시간 × 730 | 약 $3.7 | 약 5천 원 |
| S3 백업, CloudWatch 경보 | 몇 GB + 경보 1개 | 약 $1 | 약 1천 원 |
| **합계** | | **약 $198** | **약 28만 원 (부가세 포함 약 30만 원)** |

- `r7i.large`로 바꾸면 EC2가 약 $117(약 16만 원)로 줄어 합계 약 19만 원입니다.
- 서버를 꺼 두면(Stop) EC2 요금은 멈추지만 디스크와 고정 IP 요금(월 약 2만 2천 원)은 계속 나갑니다.
- 1년 이상 쓸 계획이면 Savings Plans로 EC2 요금을 30% 정도 줄일 수 있습니다.
- Route53 호스팅 영역을 쓰면 영역당 월 $0.5가 추가됩니다.

## 6. 서버에 접속하기 (SSM Session Manager)
SSH 키나 22번 포트 없이 AWS 자격 증명만으로 접속합니다. 접속 기록도 AWS에 남습니다.
`infra/terraform` 폴더에서 접속 명령을 확인합니다.
```zsh
terraform output -raw ssm_connect_command
```
출력된 `aws ssm start-session ...` 명령을 복사해서 실행합니다. 서버를 막 만든 직후라면 SSM 에이전트가 등록될 때까지 2~3분 기다려야 할 수 있습니다.

접속하면 `ssm-user`로 들어갑니다. 먼저 설치가 끝났는지 확인합니다. `status: done`이 나오면 끝난 것입니다.
```bash
cloud-init status --wait
```
진행 상황이나 오류는 이 로그에 있습니다.
```bash
sudo tail -n 50 /var/log/dartrag-bootstrap.log
```
이후 작업은 `ubuntu` 사용자로 합니다(Docker 권한이 있음).
```bash
sudo -iu ubuntu
cat /srv/dartrag/NEXT_STEPS.txt
```

## 7. `.env` 채우기
비밀값은 서버 안에서만 입력합니다. 코드 저장소, 채팅, Terraform 변수에 넣지 마세요.
```bash
cd /srv/dartrag/app
ls infra/prod
```
`infra/prod/.env.example`이 있으면 복사해서 시작합니다.
```bash
cp infra/prod/.env.example infra/prod/.env
```
편집기로 열어 값을 채웁니다(저장: Ctrl+O, Enter, 종료: Ctrl+X).
```bash
nano infra/prod/.env
chmod 600 infra/prod/.env
```
- 운영 서버이므로 `AUTH_REQUIRED=true`, `ALLOW_SIGNUP=false`, `COOKIE_SECURE=true`로 둡니다(README의 "인터넷에 공개할 때" 참고).
- Ollama는 Docker 밖(서버 본체)에서 돌아갑니다. 컨테이너에서는 `http://host.docker.internal:11434`로 접속합니다. compose 서비스에 `extra_hosts: ["host.docker.internal:host-gateway"]`가 있어야 합니다. 11434 포트는 보안 그룹에서 막혀 있어 외부에서는 닿지 않습니다.

## 8. 서비스 시작
`/srv/dartrag/app`에서 실행합니다.
```bash
docker compose -f infra/prod/docker-compose.yml up -d
docker compose -f infra/prod/docker-compose.yml ps
docker compose -f infra/prod/docker-compose.yml logs -f --tail 100
```
로그 보기는 Ctrl+C로 빠져나옵니다. 브라우저에서 `terraform output -raw service_url`에 나온 주소로 접속해 봅니다.

모델이 준비됐는지 확인하려면:
```bash
ollama list
```

새 코드를 반영할 때:
```bash
cd /srv/dartrag/app
git pull
docker compose -f infra/prod/docker-compose.yml up -d --build
```

## 9. 백업
서버의 IAM 역할로 백업 버킷에 올릴 수 있습니다(키 입력 불필요). 버킷 이름은 `terraform output -raw backup_bucket_name` 또는 `/srv/dartrag/NEXT_STEPS.txt`에 있습니다.
```bash
aws s3 cp 백업파일.dump s3://버킷이름/postgres/백업파일.dump
aws s3 ls s3://버킷이름/postgres/
```
- 백업은 30일 안에 자동으로 지워집니다(현재 버전 29일 뒤 만료, 이전 버전 1일 뒤 삭제). 기간은 `backup_retention_days`로 줄일 수 있습니다.
- 서버에는 삭제 권한이 없습니다. 서버가 해킹당해도 기존 백업을 지울 수 없게 하려는 것입니다.

## 10. 원격 상태 (선택)
기본으로 Terraform 상태는 내 컴퓨터의 `infra/terraform/terraform.tfstate`에 저장됩니다. 이 파일을 잃어버리면 Terraform이 만든 리소스를 관리할 수 없게 되므로 따로 보관하세요. git에는 올라가지 않습니다(`.gitignore`). 여러 사람이 함께 관리한다면 S3 원격 상태를 씁니다.
1. 상태 저장용 S3 버킷을 콘솔에서 하나 만들고(버전 관리 켜기) 이름을 정합니다.
2. `versions.tf`의 `backend "s3"` 주석을 풀고 버킷 이름을 고칩니다.
3. 상태를 옮깁니다.
```zsh
terraform init -migrate-state
```

## 11. 모두 지우기 (destroy)
**서버, 데이터 디스크(DB, 검색 색인, 모델)가 모두 지워집니다.** 필요한 데이터는 먼저 백업하세요.
```zsh
terraform destroy
```
- 백업 버킷에 파일이 남아 있으면 버킷 삭제에서 멈춥니다. 실수로 백업을 지우지 않게 하려는 기본값입니다. 백업까지 정말 모두 지우려면 `terraform.tfvars`에 `backup_bucket_force_destroy = true`를 넣고 `terraform apply`를 한 번 한 뒤 다시 `terraform destroy`를 실행합니다.
- 서버만 새로 만들고 데이터 디스크는 유지하려면 destroy 대신 이 명령을 씁니다.
```zsh
terraform apply -replace=aws_instance.app
```

## 문제 해결
- `no matching EC2 Subnet found` 또는 기본 VPC가 없다는 오류: 기본 VPC를 지운 계정입니다. `aws ec2 create-default-vpc`를 실행하고 다시 apply합니다.
- `Unsupported: ... instance type`: 그 가용 영역에 해당 유형이 없습니다. `availability_zone`을 `ap-northeast-2c` 등으로 바꿉니다.
- SSM 접속이 안 됨: 서버를 만든 직후면 몇 분 기다립니다. 내 컴퓨터에 `session-manager-plugin`이 설치돼 있는지 확인합니다.
- 서버를 만들 때 쓰는 설정(cloud-init)이나 Ubuntu 이미지가 바뀌어도 서버는 자동으로 다시 만들어지지 않습니다. 새로 만들려면 위의 `-replace` 명령을 씁니다.
