terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }

  # 기본값은 로컬 상태 파일(terraform.tfstate)입니다. 여러 사람이 함께 쓰거나
  # 노트북을 잃어버려도 상태를 지키고 싶으면 S3 원격 상태를 켜세요(docs/terraform.md 참고).
  # backend "s3" {
  #   bucket       = "내-테라폼-상태-버킷"
  #   key          = "dartrag/terraform.tfstate"
  #   region       = "ap-northeast-2"
  #   encrypt      = true
  #   use_lockfile = true
  # }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project   = var.project
      ManagedBy = "terraform"
    }
  }
}
