# 서버 한 대로 서비스를 운영하는 구성입니다.
#
# 네트워크는 AWS 계정마다 리전별로 미리 만들어져 있는 "기본 VPC"를 그대로 씁니다.
# 서버가 한 대뿐이고 인터넷에서 80/443으로 바로 접속받기 때문에 사설 서브넷, NAT 게이트웨이
# (월 수만 원), 라우팅 테이블을 따로 만들 이유가 없습니다. 만들 리소스가 적을수록 실수할 곳도
# 적고, destroy도 깔끔합니다. 실제 접근 통제는 아래 보안 그룹이 담당합니다.
# 기본 VPC를 지운 계정이라면 `aws ec2 create-default-vpc` 한 줄로 다시 만들 수 있습니다.

data "aws_caller_identity" "current" {}

data "aws_vpc" "default" {
  default = true
}

data "aws_subnet" "default" {
  vpc_id            = data.aws_vpc.default.id
  availability_zone = var.availability_zone
  default_for_az    = true
}

# Canonical(099720109477)이 배포하는 Ubuntu 24.04 LTS 공식 이미지 중 가장 최신 것
data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"]

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"]
  }

  filter {
    name   = "architecture"
    values = ["x86_64"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

locals {
  name           = var.project
  create_dns     = var.domain_name != "" && var.hosted_zone_id != ""
  create_alarm   = var.alarm_email != ""
  data_mount_dir = "/srv/dartrag"
}

# ---------------------------------------------------------------------------
# 보안 그룹: 80/443은 누구나, SSH는 지정한 IP만(기본은 닫힘)
# ---------------------------------------------------------------------------
resource "aws_security_group" "app" {
  name        = "${local.name}-app"
  description = "${local.name} web server"
  vpc_id      = data.aws_vpc.default.id

  tags = {
    Name = "${local.name}-app"
  }
}

resource "aws_vpc_security_group_ingress_rule" "http" {
  for_each = toset(["0.0.0.0/0", "::/0"])

  security_group_id = aws_security_group.app.id
  description       = "HTTP"
  ip_protocol       = "tcp"
  from_port         = 80
  to_port           = 80
  cidr_ipv4         = strcontains(each.value, ":") ? null : each.value
  cidr_ipv6         = strcontains(each.value, ":") ? each.value : null
}

resource "aws_vpc_security_group_ingress_rule" "https" {
  for_each = toset(["0.0.0.0/0", "::/0"])

  security_group_id = aws_security_group.app.id
  description       = "HTTPS"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  cidr_ipv4         = strcontains(each.value, ":") ? null : each.value
  cidr_ipv6         = strcontains(each.value, ":") ? each.value : null
}

resource "aws_vpc_security_group_ingress_rule" "ssh" {
  for_each = toset(var.ssh_allowed_cidrs)

  security_group_id = aws_security_group.app.id
  description       = "SSH"
  ip_protocol       = "tcp"
  from_port         = 22
  to_port           = 22
  cidr_ipv4         = strcontains(each.value, ":") ? null : each.value
  cidr_ipv6         = strcontains(each.value, ":") ? each.value : null
}

# 나가는 트래픽은 모두 허용(패키지 설치, Docker 이미지, 모델 다운로드, OpenDART API 호출)
resource "aws_vpc_security_group_egress_rule" "all" {
  for_each = toset(["0.0.0.0/0", "::/0"])

  security_group_id = aws_security_group.app.id
  description       = "All outbound"
  ip_protocol       = "-1"
  cidr_ipv4         = strcontains(each.value, ":") ? null : each.value
  cidr_ipv6         = strcontains(each.value, ":") ? each.value : null
}

# ---------------------------------------------------------------------------
# EC2 서버
# ---------------------------------------------------------------------------
resource "aws_instance" "app" {
  ami                    = data.aws_ami.ubuntu.id
  instance_type          = var.instance_type
  subnet_id              = data.aws_subnet.default.id
  vpc_security_group_ids = [aws_security_group.app.id]
  iam_instance_profile   = aws_iam_instance_profile.app.name
  key_name               = var.ssh_key_name != "" ? var.ssh_key_name : null
  ebs_optimized          = true

  # IMDSv2만 허용하고 hop 제한을 1로 둬서, Docker 컨테이너가 서버의 IAM 자격 증명을 꺼내 쓰지 못하게 합니다.
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }

  root_block_device {
    volume_type           = "gp3"
    volume_size           = var.root_volume_size_gb
    encrypted             = true
    delete_on_termination = true
  }

  # 비밀값은 절대 넣지 않습니다. user_data는 콘솔과 API로 누구나(권한이 있으면) 읽을 수 있습니다.
  user_data = templatefile("${path.module}/templates/cloud-init.yaml.tftpl", {
    data_volume_serial = replace(aws_ebs_volume.data.id, "-", "")
    data_mount_dir     = local.data_mount_dir
    repo_url           = var.repo_url
    git_ref            = var.git_ref
    ollama_model       = var.ollama_model
    backup_bucket      = aws_s3_bucket.backup.bucket
    aws_region         = var.aws_region
  })

  tags = {
    Name = "${local.name}-app"
  }

  lifecycle {
    # 새 Ubuntu 이미지가 나오거나 cloud-init을 고쳤다고 서버가 통째로 다시 만들어지지 않게 합니다.
    # 정말 새로 만들고 싶으면 `terraform apply -replace=aws_instance.app`을 쓰세요.
    ignore_changes = [ami, user_data]
  }
}

# Docker 볼륨과 모델을 담는 별도 데이터 디스크. 서버를 다시 만들어도 데이터는 남습니다.
resource "aws_ebs_volume" "data" {
  availability_zone = var.availability_zone
  type              = "gp3"
  size              = var.data_volume_size_gb
  encrypted         = true

  tags = {
    Name = "${local.name}-data"
  }
}

resource "aws_volume_attachment" "data" {
  device_name                    = "/dev/sdf"
  volume_id                      = aws_ebs_volume.data.id
  instance_id                    = aws_instance.app.id
  stop_instance_before_detaching = true
}

# 서버를 껐다 켜도 바뀌지 않는 고정 공인 IP
resource "aws_eip" "app" {
  domain   = "vpc"
  instance = aws_instance.app.id

  tags = {
    Name = "${local.name}-app"
  }
}

# ---------------------------------------------------------------------------
# 선택: 도메인 A 레코드
# ---------------------------------------------------------------------------
resource "aws_route53_record" "app" {
  count = local.create_dns ? 1 : 0

  zone_id = var.hosted_zone_id
  name    = var.domain_name
  type    = "A"
  ttl     = 300
  records = [aws_eip.app.public_ip]
}
