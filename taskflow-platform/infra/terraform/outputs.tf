output "app_url" {
  value = "${var.certificate_arn == "" ? "http" : "https"}://${aws_lb.app.dns_name}"
}

output "ecr_repository" {
  value = aws_ecr_repository.app.name
}

output "ecr_repository_url" {
  value = aws_ecr_repository.app.repository_url
}

output "ecs_cluster" {
  value = aws_ecs_cluster.main.name
}

output "ecs_service" {
  value = aws_ecs_service.app.name
}

output "admin_password_secret" {
  description = "aws secretsmanager get-secret-value --secret-id <this> to read the generated admin password"
  value       = aws_secretsmanager_secret.admin_password.name
}

output "github_deploy_role_arn" {
  value = var.github_repo == "" ? null : aws_iam_role.github_deploy[0].arn
}
