"""Agent Status TUI — Textual app with SSE subscription.

Supports multiple Status Service sources via AGENT_STATUS_URLS (comma-separated).
"""

import asyncio
import json
import os
import time
from urllib.parse import urlparse

import httpx
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.reactive import reactive
from textual.widgets import Static, Header, Footer

# --- Config ---

STATUS_PORT = 7890


def _parse_urls() -> list[str]:
    """Parse status service URLs.

    Priority:
      1. AGENT_STATUS_URLS env var (comma-separated)
      2. AGENT_STATUS_URL env var (single)
      3. Default: localhost:7890
    """
    urls_str = os.environ.get("AGENT_STATUS_URLS", "")
    if urls_str:
        return [u.strip().rstrip("/") for u in urls_str.split(",") if u.strip()]

    single = os.environ.get("AGENT_STATUS_URL", "")
    if single:
        return [single.rstrip("/")]

    return [f"http://localhost:{STATUS_PORT}"]


STATUS_URLS = _parse_urls()
STATUS_TOKEN = os.environ.get("AGENT_STATUS_TOKEN", "")
MULTI_SOURCE = len(STATUS_URLS) > 1


def _headers() -> dict:
    h = {}
    if STATUS_TOKEN:
        h["Authorization"] = f"Bearer {STATUS_TOKEN}"
    return h


STATUS_COLORS = {
    "working": "red",
    "waiting": "yellow",
    "idle": "green",
    "offline": "grey",
}


class AgentRow(Static):
    """Single-line agent status display."""

    status = reactive("offline")
    intent = reactive("")
    agent_id = reactive("")
    host = reactive("")
    name = reactive("")
    source_label = reactive("")
    autopilot = reactive(False)
    status_since = reactive(0.0)
    last_heartbeat = reactive(0.0)

    def __init__(self, agent_id: str, **kwargs):
        super().__init__(**kwargs)
        self.agent_id = agent_id
        now = time.time()
        self.status_since = now
        self.last_heartbeat = now

    def on_mount(self):
        self.set_interval(1, self.refresh)

    def render(self) -> str:
        color = STATUS_COLORS.get(self.status, "grey")
        duration = time.time() - self.status_since if self.status_since else 0
        dur_str = self._fmt(duration)
        hb_ago = time.time() - self.last_heartbeat if self.last_heartbeat else 0
        hb_str = f"{int(hb_ago)}s"

        name = self.name or self.host or self.agent_id[:16]
        intent = self.intent or ""
        # Truncate intent to fit
        max_intent = 40
        if len(intent) > max_intent:
            intent = intent[:max_intent - 1] + "…"

        # Autopilot badge
        ap = " [bold cyan]⚡autopilot[/]" if self.autopilot else ""

        # Show source label when multiple sources are configured
        source = f"  [dim italic]{self.source_label}[/]" if MULTI_SOURCE and self.source_label else ""

        return f"[bold {color}]●[/] {name}{source}{ap}  [dim]{intent}[/]  [{color}]{dur_str}[/]  [dim]♥ {hb_str}[/]"

    def _fmt(self, seconds: float) -> str:
        s = int(seconds)
        if s < 60:
            return f"{s}s"
        elif s < 3600:
            return f"{s // 60}m{s % 60:02d}s"
        else:
            return f"{s // 3600}h{(s % 3600) // 60:02d}m"

    def update_from_data(self, data: dict, source_label: str = ""):
        new_status = data.get("status", "offline")
        if new_status != self.status:
            self.status_since = time.time()
        self.status = new_status
        self.intent = data.get("intent", "")
        self.autopilot = data.get("autopilot", False)
        self.host = data.get("host", self.host)
        if data.get("name"):
            self.name = data["name"]
        if source_label:
            self.source_label = source_label
        self.last_heartbeat = time.time()


class AgentStatusApp(App):
    """Textual TUI for agent status monitoring."""

    CSS = """
    Screen {
        layout: vertical;
    }
    #agent-list {
        height: 1fr;
        padding: 0 1;
    }
    .agent-row {
        height: 1;
    }
    Footer {
        background: $surface;
    }
    """

    TITLE = "Agent Status"
    BINDINGS = [("q", "quit", "Quit"), ("r", "refresh", "Refresh"), ("d", "delete_offline", "Del Offline")]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield VerticalScroll(id="agent-list")
        yield Footer()

    def on_mount(self):
        self._agents: dict[str, AgentRow] = {}
        # Launch one SSE listener per source
        for idx, url in enumerate(STATUS_URLS):
            label = self._source_label(url)
            self.run_worker(self._sse_listener(url, label), exclusive=False, group=f"sse-{idx}")
        self.run_worker(self._initial_fetch_all(), exclusive=False)

    @staticmethod
    def _source_label(url: str) -> str:
        """Map URL to friendly label."""
        host = urlparse(url).hostname or url
        if host in ("localhost", "127.0.0.1", "::1"):
            return "local"
        return host

    async def action_refresh(self):
        await self._initial_fetch_all()

    async def action_delete_offline(self):
        """Remove all offline agents via API on all sources."""
        for url in STATUS_URLS:
            try:
                async with httpx.AsyncClient(timeout=5) as client:
                    await client.delete(f"{url}/agents/offline", headers=_headers())
            except Exception:
                pass
        # Remove offline rows from TUI immediately
        offline_ids = [aid for aid, row in self._agents.items() if row.status == "offline"]
        for aid in offline_ids:
            row = self._agents.pop(aid)
            row.remove()

    def _ns_key(self, source_url: str, agent_id: str) -> str:
        """Namespace agent_id by source URL to avoid collisions across sources."""
        if not MULTI_SOURCE:
            return agent_id
        return f"{source_url}|{agent_id}"

    def _get_or_create_row(self, key: str) -> AgentRow:
        if key not in self._agents:
            row = AgentRow(key, classes="agent-row")
            self._agents[key] = row
            self.query_one("#agent-list").mount(row)
        return self._agents[key]

    async def _initial_fetch_all(self):
        """Fetch current status from all sources in parallel."""
        tasks = [
            self._initial_fetch(url, self._source_label(url))
            for url in STATUS_URLS
        ]
        await asyncio.gather(*tasks)

    async def _initial_fetch(self, url: str, source_label: str):
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{url}/status", headers=_headers())
                if resp.status_code == 200:
                    data = resp.json()
                    for agent_data in data.get("agents", {}).values():
                        aid = agent_data.get("agent_id", "")
                        if aid:
                            key = self._ns_key(url, aid)
                            row = self._get_or_create_row(key)
                            row.update_from_data(agent_data, source_label)
        except Exception:
            pass

    async def _sse_listener(self, url: str, source_label: str):
        while True:
            try:
                async with httpx.AsyncClient(timeout=None) as client:
                    async with client.stream("GET", f"{url}/events", headers=_headers()) as resp:
                        async for line in resp.aiter_lines():
                            if line.startswith("data:"):
                                raw = line[5:].strip()
                                if raw:
                                    data = json.loads(raw)
                                    aid = data.get("agent_id", "")
                                    if not aid:
                                        continue
                                    key = self._ns_key(url, aid)
                                    if data.get("event") == "removed":
                                        row = self._agents.pop(key, None)
                                        if row:
                                            row.remove()
                                    else:
                                        row = self._get_or_create_row(key)
                                        row.update_from_data(data, source_label)
            except Exception:
                await asyncio.sleep(2)


if __name__ == "__main__":
    app = AgentStatusApp()
    app.run()
