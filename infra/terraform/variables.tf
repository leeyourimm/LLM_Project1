variable "aws_region" {
  description = "AWS 리전. 기본은 서울(ap-northeast-2)입니다."
  type        = string
  default     = "ap-northeast-2"
}

variable "availability_zone" {
  description = "서버와 데이터 볼륨을 둘 가용 영역. EBS 볼륨은 같은 가용 영역의 서버에만 붙일 수 있습니다."
  type        = string
  default     = "ap-northeast-2a"
}

variable "project" {
  description = "리소스 이름과 태그 앞에 붙는 프로젝트 이름(영문 소문자, 숫자, 하이픈)."
  type        = string
  default     = "dartrag"

  validation {
    condition     = can(regex("^[a-z0-9-]{3,20}$", var.project))
    error_message = "project는 영문 소문자, 숫자, 하이픈으로 3~20자여야 합니다."
  }
}

variable "instance_type" {
  description = "EC2 인스턴스 유형. qwen3:8b와 bge-m3를 함께 돌리려면 메모리 16GB 이상이 필요합니다. 기본 m7i.xlarge(4 vCPU, 16GB). 더 싸게 하려면 r7i.large(2 vCPU, 16GB)지만 답변이 눈에 띄게 느려집니다."
  type        = string
  default     = "m7i.xlarge"
}

variable "root_volume_size_gb" {
  description = "루트(OS) 디스크 크기(GB). 운영체제와 패키지만 들어갑니다."
  type        = number
  default     = 30
}

variable "data_volume_size_gb" {
  description = "데이터 디스크 크기(GB). /srv/dartrag에 붙고 Docker 데이터(Postgres, OpenSearch 볼륨과 이미지), Ollama 모델, 저장소 코드가 여기에 저장됩니다."
  type        = number
  default     = 100
}

variable "ssh_allowed_cidrs" {
  description = "SSH(22번 포트) 접속을 허용할 IP 대역 목록(예: [\"203.0.113.10/32\"]). 기본값인 빈 목록이면 SSH를 열지 않습니다. SSM Session Manager 접속을 권장합니다."
  type        = list(string)
  default     = []
}

variable "ssh_key_name" {
  description = "SSH 접속에 쓸 EC2 키 페어 이름. SSH를 쓰지 않으면 빈 문자열로 두세요."
  type        = string
  default     = ""
}

variable "repo_url" {
  description = "서버에 clone할 Git 저장소의 HTTPS 주소(공개 저장소). 비공개 저장소라면 서버에 접속한 뒤 직접 clone하세요."
  type        = string
  default     = "https://github.com/leeyourimm/LLM_Project1.git"
}

variable "git_ref" {
  description = "clone 후 체크아웃할 브랜치나 태그 이름."
  type        = string
  default     = "main"
}

variable "ollama_model" {
  description = "서버를 처음 켤 때 Ollama로 미리 내려받을 LLM 모델."
  type        = string
  default     = "qwen3:8b"
}

variable "backup_retention_days" {
  description = "백업 보관 기간(일). 개인정보 처리방침에서 백업은 30일 안에 삭제된다고 약속하므로 30 이하로 두세요. 덮어쓰거나 지운 이전 버전까지 이 기간 안에 사라지도록 설정됩니다."
  type        = number
  default     = 30

  validation {
    condition     = var.backup_retention_days >= 2 && var.backup_retention_days <= 30
    error_message = "backup_retention_days는 2~30 사이여야 합니다."
  }
}

variable "backup_bucket_force_destroy" {
  description = "true면 terraform destroy 때 백업 버킷 안의 백업까지 모두 지웁니다. 실수로 백업을 잃지 않도록 기본은 false이며, 정말 전부 지울 때만 true로 바꾸고 apply한 뒤 destroy하세요."
  type        = bool
  default     = false
}

variable "domain_name" {
  description = "서비스 도메인(예: dart.example.com). hosted_zone_id와 함께 채우면 Route53 A 레코드를 만들고, 비워 두면 만들지 않습니다."
  type        = string
  default     = ""
}

variable "hosted_zone_id" {
  description = "domain_name이 속한 Route53 호스팅 영역 ID(예: Z0123456789ABCDEFGHIJ). 비워 두면 DNS 레코드를 만들지 않습니다."
  type        = string
  default     = ""
}

variable "alarm_email" {
  description = "서버 상태 검사 실패 알림을 받을 이메일. 채우면 CloudWatch 경보와 SNS 이메일 구독을 만듭니다(받은 메일에서 구독 확인을 눌러야 알림이 옵니다). 비워 두면 만들지 않습니다."
  type        = string
  default     = ""
}
