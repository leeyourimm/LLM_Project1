output "public_ip" {
  description = "서버의 고정 공인 IP(Elastic IP). 도메인 DNS의 A 레코드에 이 값을 넣습니다."
  value       = aws_eip.app.public_ip
}

output "instance_id" {
  description = "EC2 인스턴스 ID"
  value       = aws_instance.app.id
}

output "backup_bucket_name" {
  description = "백업을 올릴 S3 버킷 이름"
  value       = aws_s3_bucket.backup.bucket
}

output "ssm_connect_command" {
  description = "내 컴퓨터에서 서버에 접속하는 명령(AWS CLI와 Session Manager 플러그인 필요)"
  value       = "aws ssm start-session --target ${aws_instance.app.id} --region ${var.aws_region}"
}

output "service_url" {
  description = "서비스 주소(도메인을 설정했으면 도메인, 아니면 IP)"
  value       = local.create_dns ? "https://${var.domain_name}" : "http://${aws_eip.app.public_ip}"
}
