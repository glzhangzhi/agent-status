"""Agent Status — Unified configuration loader.

Loads settings from (in priority order):
  1. config.yaml in the project root
  2. Environment variables (backward compatibility)
  3. Built-in defaults

Usage in any component:
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from config import cfg
"""

import os
import subprocess
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent
CONFIG_FILE = PROJECT_ROOT / "config.yaml"


def _load_yaml() -> dict:
    """Load config.yaml if it exists, otherwise return empty dict."""
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


def _get_local_ips() -> set[str]:
    """Get all local IP addresses."""
    try:
        result = subprocess.run(
            ["hostname", "-I"], capture_output=True, text=True, timeout=2,
        )
        if result.returncode == 0:
            return set(result.stdout.strip().split())
    except Exception:
        pass
    return set()


class _Config:
    """Centralized configuration with YAML → env var → default fallback chain."""

    def __init__(self):
        raw = _load_yaml()
        self._raw = raw

        # --- Shared ---
        self.token: str = (
            os.environ.get("AGENT_STATUS_TOKEN")
            or raw.get("token", "")
        )

        # --- Status Service ---
        svc = raw.get("service", {})
        self.service_host: str = svc.get("host", "0.0.0.0")
        self.service_port: int = int(svc.get("port", 7890))
        self.service_heartbeat_timeout: int = int(svc.get("heartbeat_timeout", 30))
        self.service_check_interval: int = int(svc.get("check_interval", 5))

        # --- tmux Poller ---
        poller = raw.get("poller", {})
        self.poller_service_url: str = (
            os.environ.get("AGENT_STATUS_URL")
            or poller.get("service_url")
            or f"http://localhost:{self.service_port}"
        )
        self.poller_poll_interval: int = int(
            os.environ.get("POLL_INTERVAL")
            or poller.get("poll_interval", 3)
        )
        self.poller_heartbeat_interval: int = int(poller.get("heartbeat_interval", 10))
        self.poller_discover_interval: int = int(poller.get("discover_interval", 30))

        # --- TUI ---
        tui = raw.get("tui", {})
        self.tui_machines: dict[str, str] = tui.get("machines", {})
        self.tui_agent_machine_ips: list[str] = tui.get("agent_machine_ips", [])
        self.tui_urls: list[str] = self._parse_tui_urls(tui)
        self.tui_multi_source: bool = len(self.tui_urls) > 1
        self.tui_local_machine_name: str = self._detect_local_name()

        # --- MCP Server ---
        mcp = raw.get("mcp", {})
        self.mcp_service_url: str = (
            os.environ.get("AGENT_STATUS_URL")
            or mcp.get("service_url")
            or f"http://localhost:{self.service_port}"
        )
        self.mcp_heartbeat_interval: int = int(mcp.get("heartbeat_interval", 10))

        # --- Web UI ---
        web = raw.get("web", {})
        self.web_sources: list[dict] = web.get("sources", [
            {"name": "local", "path": "/api/local"},
        ])

    def _parse_tui_urls(self, tui: dict) -> list[str]:
        """Parse TUI URLs with env var → yaml → auto-discovery fallback."""
        # Env var highest priority
        urls_str = os.environ.get("AGENT_STATUS_URLS", "")
        if urls_str:
            return [u.strip().rstrip("/") for u in urls_str.split(",") if u.strip()]
        single = os.environ.get("AGENT_STATUS_URL", "")
        if single:
            return [single.rstrip("/")]
        # From config: explicit URLs
        urls = tui.get("urls", [])
        if urls:
            return [u.rstrip("/") for u in urls]
        # Auto-detect: localhost + other known agent machines (not on this host)
        result = [f"http://localhost:{self.service_port}"]
        if self.tui_agent_machine_ips:
            local_ips = _get_local_ips()
            for ip in self.tui_agent_machine_ips:
                if ip not in local_ips:
                    result.append(f"http://{ip}:{self.service_port}")
        return result

    def _detect_local_name(self) -> str:
        """Detect the friendly name of this machine from configured machines map."""
        if not self.tui_machines:
            return "local"
        local_ips = _get_local_ips()
        for ip, name in self.tui_machines.items():
            if ip in local_ips:
                return name
        return "local"


cfg = _Config()
