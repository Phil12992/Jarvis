#!/usr/bin/env bash
# Erstellt den unprivilegierten LXC-Container fuer JARVIS auf dem Proxmox-Host.
# Aufruf (auf dem Proxmox-Host als root):
#   ./create-ct.sh
#
# Zielhardware: Lenovo ThinkPad mit Intel i5-2520M, 7,6 GB RAM.
# Das ist die RAM-Obergrenze, nicht ein Wunschwert. Proxmox selbst braucht
# ~1 GB, deshalb bekommt der CT bewusst nur 3 GB und swap daneben.

set -euo pipefail

CTID="${CTID:-100}"
CT_NAME="${CT_NAME:-jarvis}"
RAM_MB="${RAM_MB:-3072}"
SWAP_MB="${SWAP_MB:-1024}"
CORES="${CORES:-2}"
DISK_GB="${DISK_GB:-24}"
STORAGE="${STORAGE:-local-lvm}"
BRIDGE="${BRIDGE:-vmbr0}"
IP_CIDR="${IP_CIDR:-192.168.1.100/24}"
GATEWAY="${GATEWAY:-192.168.1.1}"
DNS="${DNS:-192.168.1.1}"
SSH_PUBKEY="${SSH_PUBKEY:-}"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "FEHLER: Muss als root laufen." >&2
  exit 1
fi

if [[ -z "$SSH_PUBKEY" ]]; then
  echo "FEHLER: SSH_PUBKEY nicht gesetzt." >&2
  echo "  Erzeuge einen Key und exportiere ihn:" >&2
  echo "    ssh-keygen -t ed25519 -C 'jarvis@proxmox'" >&2
  echo "    export SSH_PUBKEY=\"\$(cat ~/.ssh/id_ed25519.pub)\"" >&2
  exit 1
fi

# ---- Preflight -------------------------------------------------------------
HOST_MEM_MB=$(( $(awk '/MemTotal/ {print $2}' /proc/meminfo) / 1024 ))
FREE_MEM_MB=$(awk '/MemAvailable/ {print int($2/1024)}' /proc/meminfo)
HOST_CORES=$(nproc)

echo "=== Preflight ==="
echo "  Host-RAM gesamt : ${HOST_MEM_MB} MB"
echo "  Host-RAM frei   : ${FREE_MEM_MB} MB"
echo "  Host-Kerne      : ${HOST_CORES}"
echo "  CT bekommt      : ${RAM_MB} MB / ${CORES} Kerne"
echo

if [[ "$FREE_MEM_MB" -lt $(( RAM_MB + 700 )) ]]; then
  cat >&2 <<WARN
WARNUNG: Nur ${FREE_MEM_MB} MB frei, der CT fordert ${RAM_MB} MB.
  Das geht, aber andere VMs/LXC werden dadurch beengt. Pruefe mit:
    pvesh get /cluster/resources --type vm
  Fortfahren? Taste druecken, dann Enter (ueberspringen: STRG+C)
WARN
  read -r -n 1 _ </dev/tty || true
  echo
fi

if [[ "$HOST_CORES" -lt 2 ]]; then
  echo "FEHLER: Host hat nur ${HOST_CORES} Kerne." >&2
  exit 1
fi

# ---- CT anlegen ------------------------------------------------------------
if [[ -f "/etc/pve/lxc/${CTID}.conf" ]]; then
  echo "FEHLER: CT ${CTID} existiert bereits. Abbrechen." >&2
  exit 1
fi

# Aktuelles Debian 12 Template ermitteln
echo "Ermittle Debian 12 Template..."
pveam update
# pveam available Ausgabe: "system  debian-12-standard_12.12-1_amd64.tar.zst  amd64"
# Template-Name ist Spalte 2
TEMPLATE=$(pveam available --section system | awk '/debian-12-standard/ {print $2}' | tail -1)
if [[ -z "$TEMPLATE" ]]; then
  echo "FEHLER: Kein Debian 12 Template gefunden." >&2
  exit 1
fi
echo "Gefunden: $TEMPLATE"
TEMPLATE_PATH="local:vztmpl/${TEMPLATE}"
if ! pveam list local | grep -q "${TEMPLATE}"; then
  echo "Lade Template ${TEMPLATE} herunter..."
  pveam download local "${TEMPLATE}"
fi

ROOTFS="${STORAGE}:${DISK_GB}"

echo "=== Lege CT ${CTID} (${CT_NAME}) an ==="

# SSH-Key temporaer auf dem Host speichern fuer die pct-Installation
SSH_KEY_TMP=$(mktemp)
echo "$SSH_PUBKEY" > "$SSH_KEY_TMP"

pct create "$CTID" "${TEMPLATE_PATH}" \
  --hostname "$CT_NAME" \
  --arch "$(dpkg --print-architecture)" \
  --cores "$CORES" \
  --memory "$RAM_MB" \
  --swap "$SWAP_MB" \
  --rootfs "$ROOTFS" \
  --net0 "name=eth0,bridge=${BRIDGE},ip=${IP_CIDR},gw=${GATEWAY},type=veth" \
  --features "nesting=1,keyctl=1,fuse=1" \
  --onboot 1 \
  --start 0 \
  --description "JARVIS agent harness. Erstellt von deploy/create-ct.sh" \
  --ssh-public-keys "$SSH_KEY_TMP" \
  --unprivileged 1

rm -f "$SSH_KEY_TMP"

# ---- LXC-Tuning im CT-Config ----------------------------------------------
# Ohne diese Anpassungen startet dockerd im CT nicht sauber.
# In PVE-Konfigurationsdateien wird die Syntax "key: value" (Doppelpunkt)
# verwendet, nicht "key=value" (Gleichheitszeichen).
CFG="/etc/pve/lxc/${CTID}.conf"

cat >>"$CFG" <<'CONF'

# ---- von create-ct.sh angehaengt ----
lxc.apparmor.profile: unconfined
lxc.cgroup2.devices.allow: a
lxc.mount.auto: proc:rw sys:rw
CONF

# Jetzt starten
pct start "$CTID"
echo "=== CT ${CTID} gestartet ==="

echo
echo "Naechster Schritt:"
echo "  pct enter ${CTID}"
echo "  bash /root/bootstrap-ct.sh"
echo
echo "Oder gleich per SSH:"
echo "  ssh root@${IP_CIDR%%/*} 'bash bootstrap-ct.sh'"
