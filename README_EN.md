# Agent Status — Copilot CLI 状态指示灯

**[🇨🇳 中文文档](README.md)**

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

---

## Deployment Guide

### Step 1: Install

```bash
# Prerequisites: tmux, Python 3.11+, pip
tmux -V        # tmux 3.x
python3 -V     # Python 3.11+

# Clone the repo
git clone https://github.com/glzhangzhi/agent-status.git
cd agent-status

# Install Python dependencies
pip install -r requirements.txt
```

### Step 2: Configure

```bash
cp config.example.yaml config.yaml
```

Edit `config.yaml`. The **minimal config** works out of the box with all defaults:

```yaml
token: ""        # no auth
service:
  port: 7890
```

For multi-machine setups, set a shared `token`:

```yaml
token: "your-secret-token"
```

> `config.yaml` is gitignored — safe to store secrets.

### Step 3: Start Services (Single Machine)

Run in tmux so services persist after closing the terminal:

```bash
cd agent-status

# 1️⃣ Start Status Service (background tmux session)
tmux new-session -d -s status './start-service.sh'

# 2️⃣ Start tmux Poller (background tmux session)
tmux new-session -d -s poller './start-poller.sh'

# 3️⃣ Start TUI (in a visible window)
./start-tui.sh
```

> **Start order matters:** Status Service must start first. Poller and TUI can follow in any order. Copilot CLI can start anytime.

### Step 4: Verify

```bash
# ✅ Check Status Service
curl -s http://localhost:7890/status | python3 -m json.tool
# Expected: {"agents": {}}

# ✅ Check Poller logs
tmux attach -t poller
# Should see: [poller] Starting ...
# If Copilot CLI is running: [poller] Discovered: <session-name>
# Detach with Ctrl+B D

# ✅ Check TUI
tmux attach -t tui
# Should see the Agent Status dashboard with colored dots
```

If you don't have Copilot CLI running yet, start one in any tmux session — Poller will auto-discover it within 30 seconds.

---

### Web UI Deployment (Optional)

The Web UI lets you view Agent status from a browser (including mobile). Requires nginx as a reverse proxy.

#### 1. Install nginx

```bash
sudo apt update && sudo apt install -y nginx
```

#### 2. Edit nginx config

```bash
sudo cp web/nginx-agent-status.conf /etc/nginx/sites-available/agent-status
sudo nano /etc/nginx/sites-available/agent-status
```

Key changes:

```nginx
server {
    listen 80;
    server_name agent-status.example.com;  # ← your domain or IP

    root /path/to/agent-status/web;        # ← absolute path to agent-status/web

    location /api/local/ {
        proxy_pass http://127.0.0.1:7890/;  # ← must match service.port
        # ... keep other proxy settings ...
    }
}
```

#### 3. Enable and reload

```bash
sudo ln -sf /etc/nginx/sites-available/agent-status /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

#### 4. Configure Web UI sources

In `config.yaml`, add sources matching your nginx `location` paths:

```yaml
web:
  sources:
    - name: "local"
      path: "/api/local"
```

#### 5. Verify

```bash
curl -s http://localhost/
# Or open http://<your-ip-or-domain>/ in a browser
```

> **HTTPS:** Use [Certbot](https://certbot.eff.org/) (`sudo certbot --nginx`) or Cloudflare proxy.

#### 6. Sound Notifications

The Web UI plays a two-tone chime when any agent transitions from `working` to `idle` or `waiting`. Suppressed during initial page load.

> **Note:** Browsers require at least one user click before playing audio (browser security policy).

---

### Multi-Machine Setup (Optional)

Aggregate Agent status from multiple servers into one dashboard.

```
┌─── Server A ──────────────┐  ┌─── Server B ──────────────┐
│ Copilot CLI (tmux)         │  │ Copilot CLI (tmux)         │
│ Poller → Status Service    │  │ Poller → Status Service    │
│          :7890              │  │          :7890              │
└────────────┬───────────────┘  └────────────┬───────────────┘
             │ SSE                           │ SSE
             └──────────┬───────────────────┘
                        ▼
              ┌── Any machine ──────┐
              │  TUI / Web UI       │
              │  (multi-source)     │
              └─────────────────────┘
```

#### On each server:

```bash
git clone https://github.com/glzhangzhi/agent-status.git
cd agent-status
pip install -r requirements.txt
cp config.example.yaml config.yaml
# Edit config.yaml — set the same token on all machines

tmux new-session -d -s status './start-service.sh'
tmux new-session -d -s poller './start-poller.sh'
```

Ensure port 7890 is reachable across machines (firewall, Tailscale/VPN, etc.).

#### On the dashboard machine:

```yaml
# config.yaml
tui:
  urls:
    - "http://localhost:7890"
    - "http://192.168.1.100:7890"
    - "http://192.168.1.101:7890"
  machines:
    "192.168.1.100": "workstation"
    "192.168.1.101": "cloud-server"
```

```bash
./start-tui.sh
```

#### Web UI multi-source

Add an nginx `location` block for each remote source, then configure `web.sources` in `config.yaml`:

```nginx
location /api/workstation/ {
    proxy_pass http://192.168.1.100:7890/;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header Connection '';
    proxy_buffering off;
    proxy_cache off;
    proxy_read_timeout 86400s;
    chunked_transfer_encoding off;
}
```

```yaml
web:
  sources:
    - name: "local"
      path: "/api/local"
    - name: "workstation"
      path: "/api/workstation"
```

---

### Auto-Start on Boot (Optional)

```bash
crontab -e
```

```
@reboot sleep 5 && cd /path/to/agent-status && tmux new-session -d -s status './start-service.sh'
@reboot sleep 8 && cd /path/to/agent-status && tmux new-session -d -s poller './start-poller.sh'
```

---

## Usage Guide

### TUI Controls

| Key | Action |
|-----|--------|
| `q` | Quit TUI |
| `r` | Refresh (re-fetch all agent states) |
| `d` | Delete all offline agent entries |

Each agent row:

```
● tavern-optimizer  Exploring codebase  1m30s  ♥ 2s
↑                   ↑                   ↑      ↑
status dot (color)  current intent      duration  last heartbeat
```

### Common API Commands

```bash
# View all agent states
curl -s http://localhost:7890/status | python3 -m json.tool

# Subscribe to SSE stream (Ctrl+C to stop)
curl -s http://localhost:7890/events

# Delete all offline agents
curl -X DELETE http://localhost:7890/agents/offline

# View Web UI config
curl -s http://localhost:7890/web/config | python3 -m json.tool
```

Add `-H "Authorization: Bearer <token>"` if auth is enabled.

### Poller CLI

```bash
# Auto-discover (default, recommended)
./start-poller.sh

# Monitor specific tmux sessions
./start-poller.sh my-session-1 my-session-2

# Custom poll interval
cd tmux_poller && python3 poller.py --auto --interval 5
```

---

## Configuration Reference

All settings in `config.yaml`, grouped by component:

| Key | Default | Description |
|-----|---------|-------------|
| `token` | `""` | Shared Bearer token (empty = no auth) |
| **service** | | |
| `service.host` | `"0.0.0.0"` | Listen address |
| `service.port` | `7890` | Listen port |
| `service.heartbeat_timeout` | `30` | Seconds without heartbeat → offline |
| `service.check_interval` | `5` | Timeout check interval (seconds) |
| **poller** | | |
| `poller.service_url` | `http://localhost:7890` | Status Service URL |
| `poller.poll_interval` | `3` | tmux capture interval (seconds) |
| `poller.heartbeat_interval` | `10` | Heartbeat POST interval (seconds) |
| `poller.discover_interval` | `30` | Auto-discovery scan interval (seconds) |
| **tui** | | |
| `tui.urls` | `["http://localhost:7890"]` | Status Service URLs |
| `tui.machines` | `{}` | IP → friendly name map |
| `tui.agent_machine_ips` | `[]` | IPs for auto-discovery |
| **mcp** | | |
| `mcp.service_url` | `http://localhost:7890` | Status Service URL |
| `mcp.heartbeat_interval` | `10` | Heartbeat interval (seconds) |
| **web** | | |
| `web.sources` | `[{name:"local", path:"/api/local"}]` | Web UI sources (match nginx locations) |

> **Env var overrides:** `AGENT_STATUS_TOKEN`, `AGENT_STATUS_URL`, `AGENT_STATUS_URLS`, `POLL_INTERVAL` take priority over config.yaml.

---

## Technical Details

### Poller Detection Rules

| Priority | Target | Pattern | Status |
|----------|--------|---------|--------|
| 1 (highest) | Full screen | `↑/↓ to navigate · enter to select` | `waiting` |
| 2 | Bottom bar | `[●◉◎○] ... esc cancel` | `working` (+ extracts intent) |
| 3 | Bottom bar | `autopilot · / commands` | `idle` (autopilot mode) |
| 4 | Bottom bar | `/ commands · ? help` or `@ files · # issues` | `idle` |
| 5 | — | tmux capture fails | `offline` |

### Intent & Agent Name Extraction

```
● Exploring agent-status project esc cancel    tavern-optimizer · Claude Opus 4.6
  ↑                              ↑              ↑
  spinner   intent="Exploring agent-status project"   name="tavern-optimizer"
```

### Auto-Discovery

Poller scans all tmux sessions every 30s, identifying Copilot CLI by:
- `Copilot v` on screen (welcome page)
- `· Claude` on screen (model name)
- `/ commands · ? help` or `esc cancel` in the bottom bar

New sessions are auto-tracked; disappeared sessions are marked offline.

### Status Service API

| Method | Path | Description |
|--------|------|-------------|
| `POST /status` | Status update | `{agent_id, host, status, intent, name, autopilot}` |
| `POST /heartbeat` | Keep-alive | `{agent_id, host}` |
| `POST /disconnect` | Agent offline | `{agent_id}` |
| `GET /status` | All agents snapshot | JSON |
| `GET /events` | SSE stream | Optional `?agent_id=xxx` filter |
| `GET /web/config` | Web UI config | Sources & token from config.yaml |
| `GET /web` | Web dashboard | HTML |
| `DELETE /agents/offline` | Purge offline agents | — |

### State Machine

```
working ──(30s no update)──▶ idle
waiting ──(30s no update)──▶ idle
any state ──(30s no heartbeat)──▶ offline
```

---

## Troubleshooting

### Poller doesn't discover Copilot CLI sessions

```bash
tmux list-sessions
tmux capture-pane -t <session-name> -p | grep -E '(Copilot v|· Claude|commands · \? help|esc cancel)'
```

### TUI not updating

```bash
curl -s http://localhost:7890/status
curl -s http://localhost:7890/events  # should stream events, Ctrl+C to stop
```

### Status always shows offline

1. Poller not running → `ps aux | grep poller.py`
2. Status Service not running → `curl localhost:7890/status`
3. tmux session name mismatch → specify manually: `./start-poller.sh <session-name>`

### Stale agent entries

```bash
# In TUI: press 'd'
# Or via API:
curl -X DELETE http://localhost:7890/agents/offline
```

### Web UI won't load / SSE disconnects

1. Check nginx config: `sudo nginx -t`
2. Check Status Service: `curl localhost:7890/status`
3. Check nginx logs: `sudo tail /var/log/nginx/error.log`
4. Verify `web.sources[].path` matches nginx `location` paths

---

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
│   └── poller.py           # tmux screen scraper
├── tui/
│   └── app.py              # Textual terminal dashboard
└── web/
    ├── index.html           # Browser dashboard
    └── nginx-agent-status.conf  # nginx reverse proxy template
```

## License

MIT
