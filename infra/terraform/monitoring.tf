# 선택: alarm_email을 채우면 서버 상태 검사(시스템/인스턴스)가 2분 연속 실패할 때 메일을 보냅니다.

resource "aws_sns_topic" "alarm" {
  count = local.create_alarm ? 1 : 0

  name = "${local.name}-alarm"
}

resource "aws_sns_topic_subscription" "alarm_email" {
  count = local.create_alarm ? 1 : 0

  topic_arn = aws_sns_topic.alarm[0].arn
  protocol  = "email"
  endpoint  = var.alarm_email
}

resource "aws_cloudwatch_metric_alarm" "status_check" {
  count = local.create_alarm ? 1 : 0

  alarm_name          = "${local.name}-status-check-failed"
  alarm_description   = "${local.name} 서버 상태 검사 실패"
  namespace           = "AWS/EC2"
  metric_name         = "StatusCheckFailed"
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 2
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  # 서버를 일부러 꺼 두면 지표가 비는데, 그때는 경보를 울리지 않습니다.
  treat_missing_data = "missing"

  dimensions = {
    InstanceId = aws_instance.app.id
  }

  alarm_actions = [aws_sns_topic.alarm[0].arn]
  ok_actions    = [aws_sns_topic.alarm[0].arn]
}
