variable "aws_region" {
  type    = string
  default = "ap-southeast-2"
}

# Explicit values avoid EC2 DescribeAvailabilityZones discovery calls.
# Confirm these AZ names are enabled for your AWS account.
variable "availability_zones" {
  type    = list(string)
  default = ["ap-southeast-2a", "ap-southeast-2b"]
  validation {
    condition     = length(var.availability_zones) == 2 && length(distinct(var.availability_zones)) == 2
    error_message = "Provide exactly two distinct Availability Zone names in the selected region."
  }
}

# Supply an AMI ID copied from the EC2 console or obtained separately.
# This avoids Terraform calling EC2 DescribeImages during planning.
variable "ami_id" {
  type        = string
  description = "Existing x86_64 Amazon Linux 2023 AMI ID in aws_region, e.g. ami-0123456789abcdef0."
  validation {
    condition     = can(regex("^ami-[0-9a-f]{8,17}$", var.ami_id))
    error_message = "ami_id must be a valid-looking EC2 AMI ID for the configured region and x86_64 architecture."
  }
}

variable "project_name" {
  type    = string
  default = "court-service"
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,20}$", var.project_name))
    error_message = "Use 3-21 lowercase letters, digits or hyphens; begin with a letter."
  }
}

variable "instance_type" {
  type    = string
  default = "t3.small"
}

variable "volume_size_gb" {
  type    = number
  default = 10
}

variable "admin_cidr" {
  type        = string
  description = "Your public IPv4 /32 for SSH and monitoring, e.g. 203.0.113.10/32."
  validation {
    condition     = can(cidrnetmask(var.admin_cidr)) && can(regex("/32$", var.admin_cidr)) && var.admin_cidr != "0.0.0.0/32"
    error_message = "admin_cidr must be a single administrator IPv4 address (/32)."
  }
}

variable "ssh_public_key_path" {
  type        = string
  description = "Absolute path to your SSH public key."
}

variable "ssh_private_key_path" {
  type    = string
  default = "~/.ssh/id_ed25519"
}

variable "acm_certificate_arn" {
  type        = string
  default     = ""
  description = "Optional ACM certificate ARN in the same AWS region to enable HTTPS."
  validation {
    condition     = var.acm_certificate_arn == "" || var.domain_name != ""
    error_message = "Set domain_name when enabling HTTPS so the application URL matches the certificate."
  }
}

variable "acknowledge_possible_charges" {
  type        = bool
  default     = false
  description = "Set true only after reviewing AWS pricing; ALB, public IPv4, EC2, EBS and data transfer can incur charges."
}

variable "domain_name" {
  type        = string
  default     = ""
  description = "DNS name covered by the ACM certificate; point it to the ALB DNS name."
}

variable "allow_http_demo" {
  type        = bool
  default     = false
  description = "Explicit demo exception: allow HTTP login without a certificate. Traffic is unencrypted."
}
