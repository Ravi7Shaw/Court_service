resource "random_password" "admin" {
  length  = 28
  special = false
}
resource "random_password" "db" {
  length  = 32
  special = false
}
resource "random_password" "redis" {
  length  = 32
  special = false
}
resource "random_password" "jwt" {
  length  = 64
  special = false
}
resource "random_password" "grafana" {
  length  = 28
  special = false
}

resource "aws_vpc" "main" {
  cidr_block           = "10.42.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = { Name = "${var.project_name}-vpc" }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "${var.project_name}-igw" }
}

resource "aws_subnet" "public" {
  count                   = 2
  vpc_id                  = aws_vpc.main.id
  cidr_block              = ["10.42.1.0/24", "10.42.2.0/24"][count.index]
  availability_zone       = var.availability_zones[count.index]
  map_public_ip_on_launch = true
  tags                    = { Name = "${var.project_name}-public-${count.index + 1}" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }
  tags = { Name = "${var.project_name}-public-rt" }
}

resource "aws_route_table_association" "public" {
  count          = 2
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_key_pair" "deployer" {
  key_name   = "${var.project_name}-deployer"
  public_key = file(pathexpand(var.ssh_public_key_path))
}

resource "aws_security_group" "alb" {
  name        = "${var.project_name}-alb"
  description = "Public HTTP/HTTPS entry point"
  vpc_id      = aws_vpc.main.id
  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  dynamic "ingress" {
    for_each = var.acm_certificate_arn == "" ? [] : [1]
    content {
      from_port   = 443
      to_port     = 443
      protocol    = "tcp"
      cidr_blocks = ["0.0.0.0/0"]
    }
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = { Name = "${var.project_name}-alb-sg" }
}

resource "aws_security_group" "nodes" {
  name        = "${var.project_name}-nodes"
  description = "Only ALB can reach web; internal ports restricted to node group"
  vpc_id      = aws_vpc.main.id
  ingress {
    from_port       = 80
    to_port         = 80
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }
  ingress {
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [var.admin_cidr]
  }
  ingress {
    from_port   = 3000
    to_port     = 3000
    protocol    = "tcp"
    cidr_blocks = [var.admin_cidr]
  }
  ingress {
    from_port   = 9090
    to_port     = 9090
    protocol    = "tcp"
    cidr_blocks = [var.admin_cidr]
  }
  ingress {
    from_port = 8000
    to_port   = 8000
    protocol  = "tcp"
    self      = true
  }
  ingress {
    from_port = 5432
    to_port   = 5432
    protocol  = "tcp"
    self      = true
  }
  ingress {
    from_port = 6379
    to_port   = 6379
    protocol  = "tcp"
    self      = true
  }
  ingress {
    from_port = 3100
    to_port   = 3100
    protocol  = "tcp"
    self      = true
  }
  ingress {
    from_port = 9100
    to_port   = 9100
    protocol  = "tcp"
    self      = true
  }
  ingress {
    from_port = 9187
    to_port   = 9187
    protocol  = "tcp"
    self      = true
  }
  ingress {
    from_port = 9121
    to_port   = 9121
    protocol  = "tcp"
    self      = true
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = { Name = "${var.project_name}-nodes-sg" }
}

resource "aws_iam_role" "ec2" {
  name = "${var.project_name}-ec2-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.ec2.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "ec2" {
  name = "${var.project_name}-ec2-profile"
  role = aws_iam_role.ec2.name
}

locals {
  nodes = {
    primary   = 0
    secondary = 1
  }
}

resource "aws_instance" "node" {
  for_each                    = local.nodes
  ami                         = var.ami_id
  instance_type               = var.instance_type
  subnet_id                   = aws_subnet.public[each.value].id
  vpc_security_group_ids      = [aws_security_group.nodes.id]
  key_name                    = aws_key_pair.deployer.key_name
  iam_instance_profile        = aws_iam_instance_profile.ec2.name
  associate_public_ip_address = true
  monitoring                  = false
  credit_specification { cpu_credits = "standard" }
  root_block_device {
    volume_type           = "gp3"
    volume_size           = var.volume_size_gb
    encrypted             = true
    delete_on_termination = true
  }
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }
  tags = { Name = "${var.project_name}-${each.key}", Role = each.key, Project = var.project_name }
  lifecycle {
    precondition {
      condition     = var.acknowledge_possible_charges
      error_message = "Review AWS charges and re-run with -var='acknowledge_possible_charges=true'."
    }
  }
}

resource "aws_ebs_volume" "primary_data" {
  availability_zone = aws_instance.node["primary"].availability_zone
  size              = var.volume_size_gb
  type              = "gp3"
  encrypted         = true
  tags              = { Name = "${var.project_name}-primary-data" }
}

resource "aws_volume_attachment" "primary_data" {
  device_name = "/dev/sdf"
  volume_id   = aws_ebs_volume.primary_data.id
  instance_id = aws_instance.node["primary"].id
}

resource "aws_lb" "app" {
  name                       = substr("${var.project_name}-alb", 0, 32)
  load_balancer_type         = "application"
  internal                   = false
  security_groups            = [aws_security_group.alb.id]
  subnets                    = aws_subnet.public[*].id
  drop_invalid_header_fields = true
  enable_deletion_protection = false
  tags                       = { Project = var.project_name }
  lifecycle {
    precondition {
      condition     = var.allow_http_demo || (var.acm_certificate_arn != "" && var.domain_name != "")
      error_message = "Set acm_certificate_arn and domain_name for HTTPS, or explicitly enable allow_http_demo for unencrypted demo traffic."
    }
    precondition {
      condition     = var.acknowledge_possible_charges
      error_message = "Review AWS charges and re-run with -var='acknowledge_possible_charges=true'."
    }
  }
}

resource "aws_lb_target_group" "app" {
  name        = substr("${var.project_name}-tg", 0, 32)
  port        = 80
  protocol    = "HTTP"
  vpc_id      = aws_vpc.main.id
  target_type = "instance"
  health_check {
    enabled             = true
    path                = "/readyz"
    protocol            = "HTTP"
    matcher             = "200"
    interval            = 20
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }
  tags = { Project = var.project_name }
}

resource "aws_lb_target_group_attachment" "node" {
  for_each         = aws_instance.node
  target_group_arn = aws_lb_target_group.app.arn
  target_id        = each.value.id
  port             = 80
}

resource "aws_lb_listener" "http_forward" {
  count             = var.acm_certificate_arn == "" ? 1 : 0
  load_balancer_arn = aws_lb.app.arn
  port              = 80
  protocol          = "HTTP"
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.app.arn
  }
}

resource "aws_lb_listener" "http_redirect" {
  count             = var.acm_certificate_arn == "" ? 0 : 1
  load_balancer_arn = aws_lb.app.arn
  port              = 80
  protocol          = "HTTP"
  default_action {
    type = "redirect"
    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }
}

resource "aws_lb_listener" "https" {
  count             = var.acm_certificate_arn == "" ? 0 : 1
  load_balancer_arn = aws_lb.app.arn
  port              = 443
  protocol          = "HTTPS"
  certificate_arn   = var.acm_certificate_arn
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.app.arn
  }
}

resource "local_file" "ansible_inventory" {
  filename = "${path.module}/../ansible/inventory.ini"
  content = templatefile("${path.module}/inventory.tftpl", {
    primary_public    = aws_instance.node["primary"].public_ip
    secondary_public  = aws_instance.node["secondary"].public_ip
    primary_private   = aws_instance.node["primary"].private_ip
    secondary_private = aws_instance.node["secondary"].private_ip
    ssh_private_key   = pathexpand(var.ssh_private_key_path)
  })
  file_permission = "0600"
}
