packer {
  required_version = "= 1.15.4"

  required_plugins {
    qemu = {
      version = "= 1.1.6"
      source  = "github.com/hashicorp/qemu"
    }
  }
}

variable "ubuntu_iso_url" {
  type        = string
  description = "Pinned Ubuntu 24.04.x live-server ISO URL"
}

variable "ubuntu_iso_checksum" {
  type        = string
  description = "Pinned sha256 checksum from releases.ubuntu.com; required at build time"
}

variable "runner_version" {
  type        = string
  description = "Pinned GitHub Actions runner version"
}

variable "runner_sha256" {
  type        = string
  description = "Pinned sha256 for actions-runner-linux-x64 archive"
}

variable "docker_key_sha256" {
  type = string
}

variable "docker_ce_version" {
  type = string
}

variable "docker_cli_version" {
  type = string
}

variable "containerd_version" {
  type = string
}

variable "buildx_version" {
  type = string
}

variable "compose_version" {
  type = string
}

variable "node_key_sha256" {
  type = string
}

variable "node_version" {
  type = string
}

variable "trivy_key_sha256" {
  type = string
}

variable "trivy_version" {
  type = string
}

variable "php_key_sha256" {
  type = string
}

variable "php83_version" {
  type = string
}

variable "php84_version" {
  type = string
}

variable "composer_version" {
  type = string
}

variable "composer_sha384" {
  type = string
}

variable "playwright_version" {
  type = string
}

source "qemu" "ubuntu_runner" {
  iso_url          = var.ubuntu_iso_url
  iso_checksum     = "sha256:${var.ubuntu_iso_checksum}"
  output_directory = "output-sanctuary-runner"
  vm_name          = "ubuntu-24.04-runner.qcow2"
  format           = "qcow2"
  disk_size        = "16384"
  memory           = 4096
  cpus             = 2
  accelerator      = "kvm"
  headless         = true
  ssh_username     = "packer"
  ssh_password     = "packer"
  ssh_timeout      = "30m"
  shutdown_command = "echo 'packer' | sudo -S sh -c 'passwd --lock packer && systemctl poweroff'"
  http_directory   = "http"
  boot_wait        = "5s"
  boot_command = [
    "e<wait>",
    "<down><down><down><end>",
    " autoinstall ds=nocloud-net\\;s=http://{{ .HTTPIP }}:{{ .HTTPPort }}/ ---<wait>",
    "<f10>"
  ]
}

build {
  sources = ["source.qemu.ubuntu_runner"]

  provisioner "file" {
    source      = "../../runner/systemd/ci-runner-job.service"
    destination = "/tmp/ci-runner-job.service"
  }

  provisioner "file" {
    source      = "../../runner/guest/run-jit-runner.sh"
    destination = "/tmp/run-jit-runner.sh"
  }

  provisioner "file" {
    source      = "scripts/verify-image-contract.sh"
    destination = "/tmp/verify-image-contract.sh"
  }

  provisioner "file" {
    source      = "keys/ondrej-php.asc"
    destination = "/tmp/ondrej-php.asc"
  }

  provisioner "shell" {
    execute_command = "chmod +x {{ .Path }}; echo 'packer' | sudo -S env {{ .Vars }} bash '{{ .Path }}'"
    environment_vars = [
      "RUNNER_VERSION=${var.runner_version}",
      "RUNNER_SHA256=${var.runner_sha256}",
      "DOCKER_KEY_SHA256=${var.docker_key_sha256}",
      "DOCKER_CE_VERSION=${var.docker_ce_version}",
      "DOCKER_CLI_VERSION=${var.docker_cli_version}",
      "CONTAINERD_VERSION=${var.containerd_version}",
      "BUILDX_VERSION=${var.buildx_version}",
      "COMPOSE_VERSION=${var.compose_version}",
      "NODE_KEY_SHA256=${var.node_key_sha256}",
      "NODE_VERSION=${var.node_version}",
      "TRIVY_KEY_SHA256=${var.trivy_key_sha256}",
      "TRIVY_VERSION=${var.trivy_version}",
      "PHP_KEY_SHA256=${var.php_key_sha256}",
      "PHP83_VERSION=${var.php83_version}",
      "PHP84_VERSION=${var.php84_version}",
      "COMPOSER_VERSION=${var.composer_version}",
      "COMPOSER_SHA384=${var.composer_sha384}",
      "PLAYWRIGHT_VERSION=${var.playwright_version}"
    ]
    script = "scripts/install-runner.sh"
  }

  provisioner "shell" {
    execute_command = "chmod +x {{ .Path }}; echo 'packer' | sudo -S env {{ .Vars }} bash '{{ .Path }}'"
    inline          = ["bash /tmp/verify-image-contract.sh"]
  }

  provisioner "shell" {
    execute_command = "chmod +x {{ .Path }}; echo 'packer' | sudo -S env {{ .Vars }} bash '{{ .Path }}'"
    script          = "scripts/seal-image.sh"
  }

  post-processor "checksum" {
    checksum_types = ["sha256"]
    output         = "output-sanctuary-runner/SHA256SUMS"
  }
}
