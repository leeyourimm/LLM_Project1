# 서버가 쓰는 IAM 역할
# - SSM Session Manager 접속용 AWS 관리형 정책(AmazonSSMManagedInstanceCore)
# - 백업 버킷에 대한 최소 권한(목록 보기, 읽기, 올리기). 삭제 권한은 주지 않습니다.
#   서버가 털려도 기존 백업을 지울 수 없고, 오래된 백업은 수명 주기 규칙이 지웁니다.

data "aws_iam_policy_document" "ec2_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "app" {
  name               = "${local.name}-app"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume.json
}

resource "aws_iam_role_policy_attachment" "ssm_core" {
  role       = aws_iam_role.app.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

data "aws_iam_policy_document" "backup" {
  statement {
    sid       = "ListBackupBucket"
    actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
    resources = [aws_s3_bucket.backup.arn]
  }

  statement {
    sid = "ReadWriteBackupObjects"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:AbortMultipartUpload",
      "s3:ListMultipartUploadParts",
    ]
    resources = ["${aws_s3_bucket.backup.arn}/*"]
  }
}

resource "aws_iam_role_policy" "backup" {
  name   = "${local.name}-backup"
  role   = aws_iam_role.app.id
  policy = data.aws_iam_policy_document.backup.json
}

resource "aws_iam_instance_profile" "app" {
  name = "${local.name}-app"
  role = aws_iam_role.app.name
}
