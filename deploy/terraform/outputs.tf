output "application_url" {
  value = "${var.acm_certificate_arn == "" ? "http" : "https"}://${var.domain_name == "" ? aws_lb.app.dns_name : var.domain_name}"
}
output "load_balancer_dns" {
  value = aws_lb.app.dns_name
}
output "primary_public_ip" {
  value = aws_instance.node["primary"].public_ip
}
output "secondary_public_ip" {
  value = aws_instance.node["secondary"].public_ip
}
output "primary_private_ip" {
  value = aws_instance.node["primary"].private_ip
}
output "secondary_private_ip" {
  value = aws_instance.node["secondary"].private_ip
}
output "grafana_url" {
  value = "http://${aws_instance.node["secondary"].public_ip}:3000"
}
output "prometheus_url" {
  value = "http://${aws_instance.node["secondary"].public_ip}:9090"
}
output "admin_email" {
  value = "admin@court.local"
}
output "admin_password" {
  value     = random_password.admin.result
  sensitive = true
}
output "db_password" {
  value     = random_password.db.result
  sensitive = true
}
output "redis_password" {
  value     = random_password.redis.result
  sensitive = true
}
output "jwt_secret" {
  value     = random_password.jwt.result
  sensitive = true
}
output "grafana_admin_password" {
  value     = random_password.grafana.result
  sensitive = true
}
