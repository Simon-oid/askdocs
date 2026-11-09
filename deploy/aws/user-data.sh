#!/bin/bash
# EC2 user data for docs/deploy-aws.md: runs once, as root, on first boot of
# an NVIDIA GPU instance from the AWS Deep Learning Base GPU AMI (Ubuntu),
# which ships the NVIDIA driver, Docker and the NVIDIA Container Toolkit.
# Log: /var/log/askdocs-setup.log
set -euxo pipefail
exec > >(tee -a /var/log/askdocs-setup.log) 2>&1

REPO=https://github.com/Simon-oid/askdocs
REF=main                  # pin a commit or tag for a reproducible deploy
COMPOSE_VERSION=v2.29.7   # used only if the AMI has no compose plugin

nvidia-smi                # no driver, no point going on

if ! docker compose version; then
    mkdir -p /usr/local/lib/docker/cli-plugins
    curl -fsSL -o /usr/local/lib/docker/cli-plugins/docker-compose \
        "https://github.com/docker/compose/releases/download/${COMPOSE_VERSION}/docker-compose-linux-x86_64"
    chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
fi
systemctl enable --now docker   # with restart: unless-stopped, survives reboots

git clone "$REPO" /opt/askdocs
git -C /opt/askdocs checkout "$REF"
cd /opt/askdocs
docker compose -f compose.yaml -f compose.cuda.yaml up -d --build
