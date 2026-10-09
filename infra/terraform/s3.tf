# 백업 버킷
# 개인정보 처리방침에서 "백업은 30일 안에 삭제"를 약속하므로, 현재 버전은 (보관 기간 - 1)일 뒤
# 만료되고, 만료나 덮어쓰기로 생긴 이전 버전은 1일 뒤 영구 삭제됩니다. 합쳐서 보관 기간 안에
# 사라집니다. (S3 수명 주기 규칙은 비동기로 실행되어 실제 삭제가 하루 정도 늦어질 수 있습니다.)

resource "aws_s3_bucket" "backup" {
  bucket        = "${local.name}-backup-${data.aws_caller_identity.current.account_id}"
  force_destroy = var.backup_bucket_force_destroy
}

resource "aws_s3_bucket_public_access_block" "backup" {
  bucket = aws_s3_bucket.backup.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "backup" {
  bucket = aws_s3_bucket.backup.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_versioning" "backup" {
  bucket = aws_s3_bucket.backup.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "backup" {
  bucket = aws_s3_bucket.backup.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "backup" {
  bucket = aws_s3_bucket.backup.id

  # 버전 관리가 켜진 다음에 규칙을 적용해야 이전 버전 규칙이 바로 동작합니다.
  depends_on = [aws_s3_bucket_versioning.backup]

  rule {
    id     = "expire-backups"
    status = "Enabled"

    filter {}

    expiration {
      days = var.backup_retention_days - 1
    }

    noncurrent_version_expiration {
      noncurrent_days = 1
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }

  rule {
    id     = "remove-expired-delete-markers"
    status = "Enabled"

    filter {}

    expiration {
      expired_object_delete_marker = true
    }
  }
}

# 암호화되지 않은 연결(HTTP)로는 접근하지 못하게 막습니다.
data "aws_iam_policy_document" "backup_bucket" {
  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.backup.arn,
      "${aws_s3_bucket.backup.arn}/*",
    ]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "backup" {
  bucket = aws_s3_bucket.backup.id
  policy = data.aws_iam_policy_document.backup_bucket.json

  depends_on = [aws_s3_bucket_public_access_block.backup]
}
