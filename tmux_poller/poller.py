#!/usr/bin/env python3
"""Agent Status tmux Poller — passively monitors Copilot CLI via tmux screen scraping.

Replaces the MCP-based approach. Instead of relying on AI to self-report,
this script periodically captures tmux pane content and detects status
from the Copilot CLI's bottom status bar.

Status detection rules:
  - Screen contains "navigate · enter to select"  → waiting (interactive input needed)
  - Bottom bar contains "esc cancel"               → working (AI thinking/executing)
  - Bottom bar contains "commands · ? help"        → idle (waiting for user prompt)
  - tmux session not found / capture fails         → offline
"""

import argparse
import asyncio
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import cfg

import httpx

# --- Config (from unified config.yaml) ---

STATUS_URL = cfg.poller_service_url
STATUS_TOKEN = cfg.token
POLL_INTERVAL = cfg.poller_poll_interval
HEARTBEAT_INTERVAL = cfg.poller_heartbeat_interval
DISCOVER_INTERVAL = cfg.poller_discover_interval
HOST = socket.gethostname()

# --- Status detection patterns ---

# Interactive selection box (check full screen — can appear anywhere)
RE_WAITING = re.compile(r'(↑/↓ to navigate · enter to select|navigate · enter to select)')

# Working state: spinner + intent text + "esc cancel" in bottom bar
RE_WORKING = re.compile(r'[●◉◎○]\s+(.+?)\s+esc cancel')

# Idle state: alternating bottom bar hints
RE_IDLE = re.compile(r'(/ commands · \? help|@ files · # issues)')

# Autopilot idle state: "autopilot · / commands" in bottom bar
RE_AUTOPILOT_IDLE = re.compile(r'autopilot\s+·\s+/ commands')

# Agent name from right side of bottom bar: "tavern-optimizer · Claude Opus 4.6"
RE_AGENT_NAME = re.compile(r'(\S+)\s+·\s+Claude')

# Copilot CLI session fingerprint (for auto-discovery)
RE_COPILOT_FINGERPRINT = re.compile(r'(Copilot v|·\s+Claude|/ commands · \? help|autopilot · / commands|esc cancel)')


# --- HTTP helpers ---

def _headers() -> dict:
    h = {"Content-Type": "application/json"}
    if STATUS_TOKEN:
        h["Authorization"] = f"Bearer {STATUS_TOKEN}"
    return h


# --- tmux helpers ---

def list_tmux_sessions() -> list[str]:
    """Return all tmux session names."""
    try:
        result = subprocess.run(
            ["tmux", "list-sessions", "-F", "#{session_name}"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return [s.strip() for s in result.stdout.strip().split("\n") if s.strip()]
    except Exception:
        pass
    return []


def capture_pane(session_target: str) -> str | None:
    """Capture full pane content of a tmux session. Returns None on failure."""
    try:
        result = subprocess.run(
            ["tmux", "capture-pane", "-t", session_target, "-p"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return result.stdout
    except Exception:
        pass
    return None


def detect_status(content: str) -> tuple[str, str, str, bool] | None:
    """Detect Copilot CLI status from pane content.

    Returns (status, intent, agent_name, autopilot) or None if not a Copilot CLI pane.
    """
    if not content:
        return None

    # 1. Check for interactive selection box (highest priority — full screen search)
    if RE_WAITING.search(content):
        agent_name = _extract_agent_name(content)
        return ("waiting", "", agent_name, False)

    # 2. Check bottom area for working / idle
    lines = content.split("\n")
    bottom = "\n".join(lines[-5:]) if len(lines) >= 5 else content

    # Working: spinner + text + "esc cancel"
    m = RE_WORKING.search(bottom)
    if m:
        intent = m.group(1).strip()
        if intent.lower() == "working":
            intent = ""  # generic "Working" label, not a real intent
        agent_name = _extract_agent_name(bottom)
        return ("working", intent, agent_name, False)

    # Autopilot idle: "autopilot · / commands"
    if RE_AUTOPILOT_IDLE.search(bottom):
        agent_name = _extract_agent_name(bottom)
        return ("idle", "", agent_name, True)

    # Idle: "/ commands · ? help" or "@ files · # issues"
    if RE_IDLE.search(bottom):
        agent_name = _extract_agent_name(bottom)
        return ("idle", "", agent_name, False)

    return None


def _extract_agent_name(text: str) -> str:
    m = RE_AGENT_NAME.search(text)
    return m.group(1) if m else ""


def is_copilot_session(content: str) -> bool:
    """Quick check whether pane content looks like a Copilot CLI."""
    return bool(content and RE_COPILOT_FINGERPRINT.search(content))


# --- Per-session state ---

class SessionState:
    def __init__(self, session_target: str):
        self.session_target = session_target
        self.agent_id = f"{HOST}-tmux-{session_target}"
        self.status: str | None = None
        self.intent: str = ""
        self.agent_name: str = ""
        self.autopilot: bool = False
        self.last_heartbeat_post: float = 0.0


# --- Poller ---

class TmuxPoller:
    def __init__(self, sessions: list[str] | None = None, auto_discover: bool = False):
        self.auto_discover = auto_discover
        self.session_states: dict[str, SessionState] = {}
        self._last_discover: float = 0.0

        if sessions:
            for s in sessions:
                self.session_states[s] = SessionState(s)

    async def run(self):
        print(f"[poller] Starting (interval={POLL_INTERVAL}s, heartbeat={HEARTBEAT_INTERVAL}s)")
        print(f"[poller] Status Service: {STATUS_URL}")

        if self.auto_discover:
            print("[poller] Auto-discovery enabled")
            await self._discover()
        else:
            print(f"[poller] Monitoring: {list(self.session_states.keys())}")

        while True:
            try:
                # Periodic auto-discovery
                now = time.time()
                if self.auto_discover and now - self._last_discover >= DISCOVER_INTERVAL:
                    await self._discover()

                # Poll each tracked session
                for state in list(self.session_states.values()):
                    await self._poll_session(state)

            except Exception as e:
                print(f"[poller] Error: {e}")

            await asyncio.sleep(POLL_INTERVAL)

    # --- Discovery ---

    async def _discover(self):
        """Scan all tmux sessions for Copilot CLI instances."""
        self._last_discover = time.time()
        all_sessions = list_tmux_sessions()
        found: set[str] = set()

        for s in all_sessions:
            content = capture_pane(s)
            if content and is_copilot_session(content):
                found.add(s)
                if s not in self.session_states:
                    print(f"[poller] Discovered: {s}")
                    self.session_states[s] = SessionState(s)

        # Mark disappeared sessions offline
        for s in list(self.session_states.keys()):
            if s not in found:
                state = self.session_states[s]
                if state.status not in ("offline", None):
                    print(f"[poller] {s}: disappeared → offline")
                    state.status = "offline"
                    await self._post_disconnect(state)

    # --- Polling ---

    async def _poll_session(self, state: SessionState):
        content = capture_pane(state.session_target)

        if content is None:
            if state.status not in ("offline", None):
                print(f"[poller] {state.session_target}: capture failed → offline")
                state.status = "offline"
                await self._post_disconnect(state)
            return

        result = detect_status(content)
        if result is None:
            return

        new_status, intent, agent_name, autopilot = result

        if agent_name and agent_name != state.agent_name:
            state.agent_name = agent_name

        now = time.time()

        # Status, intent, or autopilot changed → POST /status
        if new_status != state.status or intent != state.intent or autopilot != state.autopilot:
            old = state.status or "init"
            state.status = new_status
            state.intent = intent
            state.autopilot = autopilot
            state.last_heartbeat_post = now
            await self._post_status(state)
            detail = f" ({intent})" if intent else ""
            ap = " [autopilot]" if autopilot else ""
            print(f"[poller] {state.session_target}: {old} → {new_status}{detail}{ap}")

        # No change but heartbeat due → POST /heartbeat
        elif now - state.last_heartbeat_post >= HEARTBEAT_INTERVAL:
            state.last_heartbeat_post = now
            await self._post_heartbeat(state)

    # --- HTTP ---

    async def _post_status(self, state: SessionState):
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                await client.post(
                    f"{STATUS_URL}/status",
                    json={
                        "agent_id": state.agent_id,
                        "host": HOST,
                        "status": state.status,
                        "intent": state.intent,
                        "name": state.agent_name,
                        "autopilot": state.autopilot,
                    },
                    headers=_headers(),
                )
        except Exception:
            pass

    async def _post_heartbeat(self, state: SessionState):
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                await client.post(
                    f"{STATUS_URL}/heartbeat",
                    json={"agent_id": state.agent_id, "host": HOST},
                    headers=_headers(),
                )
        except Exception:
            pass

    async def _post_disconnect(self, state: SessionState):
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                await client.post(
                    f"{STATUS_URL}/disconnect",
                    json={"agent_id": state.agent_id},
                    headers=_headers(),
                )
        except Exception:
            pass


# --- Entry point ---

async def main():
    global POLL_INTERVAL

    parser = argparse.ArgumentParser(
        description="Monitor Copilot CLI status via tmux screen scraping",
    )
    parser.add_argument(
        "sessions", nargs="*",
        help="tmux session targets to monitor (e.g. test-6 tavern-3)",
    )
    parser.add_argument(
        "--auto", action="store_true",
        help="Auto-discover Copilot CLI sessions (default if no sessions given)",
    )
    parser.add_argument(
        "--interval", type=int, default=POLL_INTERVAL,
        help=f"Poll interval in seconds (default: {POLL_INTERVAL})",
    )
    args = parser.parse_args()

    POLL_INTERVAL = args.interval

    auto = args.auto or not args.sessions
    poller = TmuxPoller(sessions=args.sessions or None, auto_discover=auto)
    await poller.run()


if __name__ == "__main__":
    asyncio.run(main())
