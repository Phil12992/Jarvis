"""Homelab-Tools.

Zwei bewusst getrennte Zugriffswege:

1. Proxmox ueber die HTTPS-API mit API-Token. Das ist der saubere Weg,
   weil der Token eine exakt definierte Rolle hat und man ihm im Proxmox
   jeden Tag einen eingeschraenkten Rollenwechsel aufloesen kann. Alle
   Proxmox-Tools hier sind read-only.

2. Lokales Docker im CT. Der docker.sock ist in den Container gemountet.
   Weil der CT unprivileged ist, gibt das keine Eskalation auf den Host
   heraus - aber es ist Arbeitsberechtigung im CT. Wer das nicht will,
   entfernt den Mount in docker-compose.yml und die docker-Tools melden
   sich dann als deaktiviert.

Der Host-Shell-Zugriff ist bewusst nicht implementiert. Wer Code auf dem
Host ausfuehren will, braucht den delegierten Code-Runner, siehe README.
"""

from __future__ import annotations

import json
import shutil
import subprocess  # noqa: S404 - nur fest verdrahtete docker-Argumente
from typing import Any

import httpx

from jarvis.config import get_settings
from jarvis.tools.registry import Tier, tool

NO_DOCKER = shutil.which("docker") is None

_client: httpx.AsyncClient | None = None


def _pve() -> httpx.AsyncClient | None:
    """Proxmox-API-Client, oder None wenn nicht konfiguriert."""
    global _client
    s = get_settings()
    if not s.homelab_enabled:
        return None
    if _client is None:
        # Proxmox API-Token erfordert einen Authorization-Header im Format:
        # PVEAPIToken=USER@REALM!TOKENID=SECRET
        # HTTP Basic Auth waere fehlerhaft und wuerde mit 401 abgelehnt.
        headers = {
            "Authorization": f"PVEAPIToken={s.proxmox_user}!{s.proxmox_token_id}={s.proxmox_token_secret}"
        }
        _client = httpx.AsyncClient(
            base_url=f"https://{s.proxmox_host}:8006/api2/json",
            verify=s.proxmox_verify_ssl,
            headers=headers,
            timeout=20.0,
        )
    return _client



async def _pve_get(path: str) -> Any:
    c = _pve()
    if c is None:
        return {"fehler": "Proxmox ist nicht konfiguriert. Setze PROXMOX_HOST und PROXMOX_TOKEN_SECRET in der .env."}
    try:
        r = await c.get(path)
    except httpx.HTTPError as exc:
        return {"fehler": f"Proxmox nicht erreichbar: {type(exc).__name__}: {exc}"}
    if r.status_code == 401:
        return {"fehler": "Proxmox-Token abgelehnt. Rolle, Realm oder Token-ID prüfen."}
    if r.status_code == 403:
        return {"fehler": "Dem Token fehlt die Berechtigung für diesen Pfad."}
    if r.status_code >= 400:
        return {"fehler": f"HTTP {r.status_code}: {r.text[:300]}"}
    return r.json().get("data")


# ---------------------------------------------------------------------------
# Proxmox, read-only
# ---------------------------------------------------------------------------


@tool(
    "proxmox_status",
    "Proxmox-Cluster-Status: welche VMs und Container laufen, mit CPU- und RAM-Nutzung.",
    {"type": "object", "properties": {}, "required": []},
    Tier.SAFE,
)
async def pve_status() -> str:
    data = await _pve_get("/cluster/resources?type=vm")
    if isinstance(data, dict):
        return str(data.get("fehler", data))

    rows: list[dict[str, Any]] = data or []
    if not rows:
        return "Keine VMs oder Container auf dem Cluster."

    lines = ["VM und Container auf dem Cluster:", ""]
    for r in sorted(rows, key=lambda x: (x.get("type", ""), x.get("node", ""), str(x.get("vmid", "")))):
        status = str(r.get("status", "?"))
        mark = "OK  " if status == "running" else f"{status[:4]:<4}"
        lines.append(
            f"  [{mark}] {r.get('type', '?'):<4} {str(r.get('name', '?'))[:20]:<20} "
            f"node={r.get('node', '?'):<8} vmid={r.get('vmid', '?'):<4} "
            f"cpu={r.get('cpu', 0) * 100:5.1f}%  ram={r.get('mem', 0) / 1024**2:6.0f} MB"
        )
    return "\n".join(lines)


@tool(
    "proxmox_node_health",
    "RAM, Disk, Uptime und Load eines einzelnen Proxmox-Nodes.",
    {
        "type": "object",
        "properties": {"node": {"type": "string", "description": "Node-Name, z.B. pve"}},
        "required": ["node"],
    },
    Tier.SAFE,
)
async def pve_node_health(node: str) -> str:
    data = await _pve_get(f"/nodes/{node}/status")
    if isinstance(data, dict) and "fehler" in data:
        return str(data["fehler"])

    mem = data.get("memory", {})
    root = data.get("rootfs", {})
    load = data.get("loadavg", [])
    return "\n".join(
        [
            f"Node {node}",
            f"  Uptime     : {data.get('uptime', 0) // 3600} h",
            f"  Load       : {' / '.join(str(x) for x in load) or '?'}",
            f"  RAM        : {mem.get('used', 0) / 1024**3:.1f} / {mem.get('total', 0) / 1024**3:.1f} GB",
            f"  Disk /     : {root.get('used', 0) / 1024**3:.1f} / {root.get('total', 0) / 1024**3:.1f} GB",
        ]
    )


@tool(
    "proxmox_storage",
    "Belegung aller Storage-Pools auf dem Proxmox-Host.",
    {"type": "object", "properties": {}, "required": []},
    Tier.SAFE,
)
async def pve_storage() -> str:
    data = await _pve_get("/storage")
    if isinstance(data, dict):
        return str(data["fehler"])
    lines = ["Storage-Pools:", ""]
    for s in data or []:
        total = s.get("total", 0) / 1024**3
        used = s.get("used", 0) / 1024**3
        pct = used / total * 100 if total else 0.0
        lines.append(
            f"  {str(s.get('storage', '?')):<10} {used:6.1f} / {total:6.1f} GB ({pct:5.1f}%)  {s.get('type', '?')}"
        )
    return "\n".join(lines)


@tool(
    "proxmox_lxc_logs",
    "Systemlog eines Containers auf dem Proxmox-Host lesen.",
    {
        "type": "object",
        "properties": {
            "vmid": {"type": "integer", "description": "VMID des Containers"},
            "node": {"type": "string", "description": "Name des Nodes, Default: pve"},
            "lines": {"type": "integer", "description": "Zeilenzahl, Default 80"},
        },
        "required": ["vmid"],
    },
    Tier.SAFE,
)
async def pve_lxc_logs(vmid: int, node: str = "pve", lines: int = 80) -> str:
    data = await _pve_get(f"/nodes/{node}/lxc/{vmid}/log?max={max(10, min(lines, 500))}")
    if isinstance(data, dict):
        return str(data.get("fehler", data))
    out = [f"{l.get('n', '')} {l.get('t', '')}" for l in (data or [])]
    return "\n".join(out) or "Leeres Log."



# ---------------------------------------------------------------------------
# Docker im CT
# ---------------------------------------------------------------------------


@tool(
    "docker_ps",
    "Container in diesem CT auflisten.",
    {
        "type": "object",
        "properties": {"all_": {"type": "boolean", "description": "Auch gestoppte Container zeigen"}},
        "required": [],
    },
    Tier.SAFE,
    enabled=not NO_DOCKER,
)
async def docker_ps(all_: bool = False) -> str:
    if NO_DOCKER:
        return "docker ist in diesem CT nicht verfügbar."
    cmd = ["docker", "ps", "--format", "{{.Names}}\t{{.Status}}\t{{.Image}}"]
    if all_:
        cmd.insert(2, "-a")
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=15, check=False)  # noqa: S603
    if out.returncode != 0:
        return f"docker ps fehlgeschlagen: {out.stderr.strip()[:300]}"
    return out.stdout.strip() or "Keine Container."


@tool(
    "docker_logs",
    "Letzte Logzeilen eines Containers in diesem CT lesen.",
    {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Containername"},
            "lines": {"type": "integer", "description": "Wie viele Zeilen, Default 100"},
        },
        "required": ["name"],
    },
    Tier.SAFE,
    enabled=not NO_DOCKER,
)
async def docker_logs(name: str, lines: int = 100) -> str:
    if NO_DOCKER:
        return "docker ist in diesem CT nicht verfügbar."
    out = subprocess.run(  # noqa: S603
        ["docker", "logs", "--tail", str(max(10, min(lines, 1000))), name],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    text = (out.stdout + out.stderr).strip()
    return text[-8000:] or "Keine Logausgabe."


def pve_ready() -> bool:
    return get_settings().homelab_enabled
