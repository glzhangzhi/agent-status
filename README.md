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

### Configuration

Copy the template and edit:

```bash
cp config.example.yaml config.yaml
# Edit config.yaml — set token, port, multi-source URLs, etc.
```

All components read from this single file. `config.yaml` is gitignored so you can safely store tokens.

<details>
<summary>config.yaml example</summary>

```yaml
token: "your-secret-token"   # shared auth (empty = no auth)

service:
  host: "0.0.0.0"
  port: 7890
  heartbeat_timeout: 30
  check_interval: 5

poller:
  service_url: "http://localhost:7890"
  poll_interval: 3
  heartbeat_interval: 10
  discover_interval: 30

tui:
  urls:
    - "http://localhost:7890"
    - "http://192.168.1.100:7890"
  machines:                        # IP → friendly name
    "192.168.1.100": "remote-box"
  agent_machine_ips:               # auto-discovery (when urls is empty)
    - "192.168.1.100"

mcp:
  service_url: "http://localhost:7890"
  heartbeat_interval: 10

web:
  sources:
    - name: "local"
      path: "/api/local"
```
</details>

> **Backward compatible:** env vars `AGENT_STATUS_TOKEN`, `AGENT_STATUS_URL`, `AGENT_STATUS_URLS`, `POLL_INTERVAL` still work and override config.yaml.

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
| `GET /web/config` | Web UI config | Returns sources & token from config.yaml |
| `GET /web` | Serve web dashboard | HTML |
| `DELETE /agents/offline` | Purge offline agents | — |

**Config:** `service.host`, `service.port`, `service.heartbeat_timeout`, `service.check_interval`, `token`

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

**Config:** `poller.service_url`, `poller.poll_interval`, `poller.heartbeat_interval`, `poller.discover_interval`, `token`

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

**Multi-source:** Monitor multiple machines via `config.yaml`:
```yaml
tui:
  urls:
    - "http://localhost:7890"
    - "http://192.168.1.100:7890"
  machines:
    "192.168.1.100": "remote-box"
```

**Config:** `tui.urls`, `tui.machines`, `tui.agent_machine_ips`, `token`

### 4. Web UI (`web/index.html`)

Browser-based dashboard with the same functionality as the TUI. Connects via SSE through nginx reverse proxy. Config is loaded dynamically from `/web/config`.

Deploy with nginx (`web/nginx-agent-status.conf`):
```bash
# Edit the config: set server_name, root path, and source proxy blocks
sudo ln -sf $(pwd)/web/nginx-agent-status.conf /etc/nginx/sites-enabled/agent-status
sudo nginx -t && sudo systemctl reload nginx
```

Configure sources in `config.yaml`:
```yaml
web:
  sources:
    - name: "local"
      path: "/api/local"
    - name: "remote"
      path: "/api/remote"
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
├── config.py               # Unified config loader (YAML → env → defaults)
├── config.example.yaml     # Config template (copy to config.yaml)
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
    ├── index.html           # Browser dashboard (loads config from /web/config)
    └── nginx-agent-status.conf  # nginx reverse proxy template
```

## License

MIT
