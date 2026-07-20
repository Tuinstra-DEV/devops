#!/usr/bin/env bash
set -euo pipefail

sudo systemctl disable ssh.service ssh.socket || true
sudo passwd --lock packer
sudo rm -f /etc/ssh/ssh_host_* /root/.ssh/authorized_keys /home/packer/.ssh/authorized_keys
sudo cloud-init clean --logs --machine-id --seed
sudo truncate -s 0 /etc/machine-id
sudo rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*
history -c || true
