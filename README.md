# Agent Status — Copilot CLI 状态指示灯

Real-time status monitoring for [GitHub Copilot CLI](https://githubnext.com/projects/copilot-cli/) agents running in tmux sessions. Zero-intrusion — the agent is completely unaware it's being watched.

```
┌──────────────┐  capture-pane   ┌──────────────┐  POST /status   ┌────────────────┐
│ Copilot CLI   │ ◀─────────────│ tmux Poller  │ ─────────────▶ │ Status Service │
│ (tmux session)│  screen scrape  │ (poller.py)  │  heartbeat     │ FastAPI :7890  │
└──────────────┘                └──────────────┘                └───────┬────────┘
                                                                       │ SSE /events
                                                              ┌────────▼────────┐
                                                              │  TUI / Web UI   │
                                                              └─────────────────┘
```

## Status Types

| Color | Status | Meaning |
|-------|--------|---------|
| 🔴 Red | `working` | Agent is thinking or executing tool calls |
| 🟢 Green | `idle` | Agent is idle, waiting for user input |
| 🟡 Yellow | `waiting` | Agent has popped up an interactive selection, waiting for user action |
| ⚡ Cyan | `idle` + autopilot | Agent is in autopilot mode, idle |
| 🔘 Grey | `offline` | Agent is offline (tmux session gone or exited) |

## Quick Start

### Prerequisites

```bash
pip install -r requirements.txt
# Requires: tmux, Python 3.11+
```

### Launch (recommended: in tmux)

```bash
# 1. Start Status Service
tmux new-session -d -s status './start-service.sh'

# 2. Start tmux Poller (auto-discovers Copilot CLI sessions)
tmux new-session -d -s poller './start-poller.sh'

# 3. Start TUI (in a visible window)
./start-tui.sh

# 4. Use Copilot CLI in any tmux session — Poller detects it automatically
```

**Start order:** Status Service → Poller → TUI. Copilot CLI can start anytime.

## Components

### 1. Status Service (`status_service/app.py`)

FastAPI server that aggregates status from pollers and pushes to clients via SSE.

| Method | Path | Description |
|--------|------|-------------|
| `POST /status` | Receive status update | `{agent_id, host, status, intent, name, autopilot}` |
| `POST /heartbeat` | Keep-alive | `{agent_id, host}` |
| `POST /disconnect` | Agent offline | `{agent_id}` |
| `GET /status` | Snapshot all agents | JSON |
| `GET /events` | SSE stream | Optional `?agent_id=xxx` filter |
| `GET /web` | Serve web dashboard | HTML |
| `DELETE /agents/offline` | Purge offline agents | — |

**Timeout:** 30s without heartbeat → offline.

**Env vars:** `AGENT_STATUS_TOKEN` (Bearer auth, empty = no auth)

### 2. tmux Poller (`tmux_poller/poller.py`)

Periodically runs `tmux capture-pane` and detects Copilot CLI status from the bottom status bar via regex matching.

| Priority | Pattern | Status |
|----------|---------|--------|
| 1 (highest) | `↑/↓ to navigate · enter to select` | `waiting` |
| 2 | `[●◉◎○] ... esc cancel` | `working` (+ extracts intent text) |
| 3 | `autopilot · / commands` | `idle` (autopilot mode) |
| 4 | `/ commands · ? help` or `@ files · # issues` | `idle` |
| 5 | tmux capture fails | `offline` |

Auto-discovers Copilot CLI sessions every 30s. Also extracts agent name from the bottom bar (e.g. `tavern-optimizer · Claude Opus 4.6`).

```bash
# Auto-discover (default)
python3 poller.py

# Monitor specific sessions
python3 poller.py my-session-1 my-session-2

# Custom interval
python3 poller.py --auto --interval 5
```

**Env vars:** `AGENT_STATUS_URL` (default `http://localhost:7890`), `AGENT_STATUS_TOKEN`, `POLL_INTERVAL`

### 3. TUI (`tui/app.py`)

Terminal dashboard built with [Textual](https://textual.textualize.io/). Subscribes to Status Service via SSE.

```
⭘                       Agent Status                    21:10:31
 ● tavern-optimizer                           1m30s  ♥ 2s
 ● code-helper  ⚡autopilot                    45s  ♥ 1s
 ● reviewer     Exploring codebase            12s   ♥ 2s

 q Quit  r Refresh  d Del Offline                       ^p palette
```

**Keys:** `q` quit, `r` refresh, `d` delete offline agents

**Multi-source:** Monitor multiple machines by setting `AGENT_STATUS_URLS` (comma-separated):
```bash
export AGENT_STATUS_URLS="http://localhost:7890,http://192.168.1.100:7890"
./start-tui.sh
```

**Env vars:** `AGENT_STATUS_URLS`, `AGENT_STATUS_URL` (single, fallback), `AGENT_STATUS_TOKEN`

### 4. Web UI (`web/index.html`)

Browser-based dashboard with the same functionality as the TUI. Pure frontend, connects via SSE through nginx reverse proxy.

Deploy with nginx (`web/nginx-agent-status.conf`):
```bash
# Edit the config: set server_name, root path, and source proxy blocks
sudo ln -sf $(pwd)/web/nginx-agent-status.conf /etc/nginx/sites-enabled/agent-status
sudo nginx -t && sudo systemctl reload nginx
```

Configure sources in `web/index.html`:
```javascript
const SOURCES = [
  { name: 'local', path: '/api/local' },
  // { name: 'remote', path: '/api/remote' },
];
```

## Multi-Machine Setup

```
┌─── Server A ──────────────┐  ┌─── Server B ──────────────┐
│ Copilot CLI (tmux)         │  │ Copilot CLI (tmux)         │
│ Poller → Status Service    │  │ Poller → Status Service    │
│          :7890              │  │          :7890              │
└────────────┬───────────────┘  └────────────┬───────────────┘
             │ SSE                           │ SSE
             └──────────┬───────────────────┘
                        ▼
              ┌── Any machine ──┐
              │  TUI / Web UI   │
              │  (multi-source) │
              └─────────────────┘
```

Each server runs its own Status Service + Poller. TUI/Web UI subscribes to all sources simultaneously.

## Directory Structure

```
agent-status/
├── start-service.sh        # Start Status Service
├── start-tui.sh            # Start TUI
├── start-poller.sh         # Start tmux Poller
├── requirements.txt        # Python dependencies
├── status_service/
│   └── app.py              # FastAPI status aggregation service
├── tmux_poller/
│   └── poller.py           # tmux screen scraper (core detection)
├── tui/
│   └── app.py              # Textual terminal dashboard
└── web/
    ├── index.html           # Browser dashboard (single-page)
    └── nginx-agent-status.conf  # nginx reverse proxy template
```

## License

MIT
