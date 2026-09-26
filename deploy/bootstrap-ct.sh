#!/usr/bin/env bash
# Installiert Docker CE im laufenden JARVIS-Container.
# Aufruf innerhalb des CT:  bash bootstrap-ct.sh
#
# Voraussetzung: create-ct.sh wurde mit features nesting,keyctl,fuse angelegt.
# Ohne diese Features bricht dockerd mit "operation not permitted" ab.

set -euo pipefail

echo "=== 1/5 Basis-Pakete ==="
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq --no-install-recommends \
  ca-certificates curl gnupg git jq less unzip

echo "=== 2/5 Docker GPG-Key ==="
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/debian/gpg \
  | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg

echo "=== 3/5 Docker APT-Repo ==="
CODENAME=$(. /etc/os-release && echo "$VERSION_CODENAME")
ARCH=$(dpkg --print-architecture)
echo "  Debian ${CODENAME} / ${ARCH}"

printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian %s stable\n' \
  "$ARCH" "$CODENAME" > /etc/apt/sources.list.d/docker.list

apt-get update -qq

echo "=== 4/5 Docker installieren ==="
apt-get install -y -qq --no-install-recommends \
  docker-ce docker-ce-cli containerd.io \
  docker-buildx-plugin docker-compose-plugin

echo "=== 5/5 Sanity-Check ==="
systemctl enable --now docker
systemctl is-active --quiet docker || {
  echo "FEHLER: dockerd laeuft nicht. Diagnose:" >&2
  echo "  journalctl -u docker -n 50 --no-pager" >&2
  echo "  pct config <CTID> | grep -E 'features|apparmor'" >&2
  exit 1
}

docker run --rm hello-world

cat <<'INFO'

============================================================
 Docker laeuft im CT.
 Naechster Schritt: JARVIS-Stack starten
============================================================

  cd /opt/jarvis
  cp .env.example .env
  nano .env          <- OPENROUTER_API_KEY + TELEGRAM_BOT_TOKEN eintragen
  docker compose up -d

============================================================
INFO
