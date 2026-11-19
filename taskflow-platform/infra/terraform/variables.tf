variable "region" {
  type    = string
  default = "ap-south-1"
}

variable "name" {
  type    = string
  default = "taskflow"
}

variable "desired_count" {
  description = "Number of app containers. Real-time updates work across containers (events go through the database)."
  type        = number
  default     = 2
}

variable "container_cpu" {
  type    = number
  default = 512
}

variable "container_memory" {
  type    = number
  default = 1024
}

variable "db_instance_class" {
  type    = string
  default = "db.t4g.micro"
}

variable "admin_email" {
  type    = string
  default = "admin@example.com"
}

variable "certificate_arn" {
  description = "ACM certificate ARN. When set, the load balancer serves HTTPS and redirects HTTP to it. Strongly recommended."
  type        = string
  default     = ""
}

variable "github_repo" {
  description = "owner/repo allowed to deploy through GitHub OIDC. Leave empty to skip creating the deploy role."
  type        = string
  default     = ""
}
