provider "aws" {
  region = "ap-southeast-2"
}

# Fetch latest Amazon Linux 2023 AMI dynamically
data "aws_ami" "amazon_linux" {
  most_recent = true

  owners = ["amazon"]

  filter {
    name   = "name"
    values = ["al2023-ami-*-x86_64"]
  }
}

# Create key pair using your local SSH public key
resource "aws_key_pair" "deployer_key" {
  key_name   = "terraform-key"
  public_key = file("~/.ssh/id_rsa.pub")
}

# Security group to allow SSH and Jenkins access
resource "aws_security_group" "jenkins_sg" {
  name        = "jenkins-sg"
  description = "Allow SSH and Jenkins access"

  ingress {
    description = "SSH"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "Jenkins"
    from_port   = 8080
    to_port     = 8080
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "Grafana NodePort"
    from_port   = 30300
    to_port     = 30300
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "Kubernetes API"
    from_port   = 6443
    to_port     = 6443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_iam_role" "jenkins_role" {
  name = "jenkins-ecr-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17",
    Statement = [
      {
        Effect = "Allow",
        Principal = {
          Service = "ec2.amazonaws.com"
        },
        Action = "sts:AssumeRole"
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "jenkins_ecr_policy" {
  role       = aws_iam_role.jenkins_role.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryFullAccess"
}

resource "aws_iam_instance_profile" "jenkins_profile" {
  name = "jenkins-instance-profile"
  role = aws_iam_role.jenkins_role.name
}

# EC2 instance with key pair and security group
resource "aws_instance" "jenkins_server" {
  ami           = data.aws_ami.amazon_linux.id
  instance_type = "c7i-flex.large"

  key_name = aws_key_pair.deployer_key.key_name

  iam_instance_profile = aws_iam_instance_profile.jenkins_profile.name

  metadata_options {
    http_tokens = "required"
  }

  vpc_security_group_ids = [
    aws_security_group.jenkins_sg.id
  ]

  tags = {
    Name = "jenkins-server"
  }

  lifecycle {
    prevent_destroy = true
  }
}

# Allocate Elastic IP
resource "aws_eip" "jenkins_eip" {
  domain = "vpc"
}

# Associate Elastic IP with EC2 instance
resource "aws_eip_association" "jenkins_eip_assoc" {
  instance_id   = aws_instance.jenkins_server.id
  allocation_id = aws_eip.jenkins_eip.id
}

# Output public IP of EC2
output "ec2_public_ip" {
  value = aws_eip.jenkins_eip.public_ip
} 

resource "local_file" "ansible_inventory" {
  content = <<EOT
[web]
${aws_eip.jenkins_eip.public_ip} ansible_user=ec2-user ansible_ssh_private_key_file=~/.ssh/id_rsa
EOT

  filename = "${path.module}/../ansible/hosts"
}