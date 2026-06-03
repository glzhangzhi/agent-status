# Agent Status — Copilot CLI 状态指示灯

**[🇬🇧 English](README_EN.md)**

实时监控运行在 tmux 中的 [GitHub Copilot CLI](https://githubnext.com/projects/copilot-cli/) Agent 工作状态。零侵入 — Agent 完全无感知，不需要调用任何工具或上报状态。

```
┌───────────────┐  capture-pane ┌──────────────┐  POST /status   ┌────────────────┐
│ Copilot CLI   │ ◀─────────────│ tmux Poller  │ ─────────────▶  │ Status Service │
│ (tmux session)│  被动读取屏幕 │ (轮询脚本)   │  POST /heartbeat│ FastAPI :7890  │
└───────────────┘               └──────────────┘                 └───────┬────────┘
                                                                         │ SSE /events
                                                                  ┌──────▼────────┐
                                                                  │ TUI / Web UI  │
                                                                  │ (状态指示灯)  │
                                                                  └───────────────┘
```

## 五种状态

| 灯色 | 状态 | 含义 |
|------|------|------|
| 🔴 红色 | `working` | Agent 正在思考或执行工具调用 |
| 🟢 绿色 | `idle` | Agent 空闲，等待用户输入新指令 |
| 🟡 黄色 | `waiting` | Agent 弹出了交互选择框，等待用户操作 |
| ⚡ 青色 | `idle` + autopilot | Agent 处于 autopilot 模式，空闲中 |
| 🔘 灰色 | `offline` | Agent 不在线（session 不存在或已退出） |

## 快速开始

### 前置要求

```bash
pip install -r requirements.txt
# 需要: tmux, Python 3.11+
```

### 配置

复制配置模板并编辑：

```bash
cp config.example.yaml config.yaml
# 编辑 config.yaml，设置 token、端口、多源 URL 等
```

所有组件共用这一个配置文件，不再需要分别设置环境变量。`config.yaml` 已被 `.gitignore` 忽略，可以安全存放 token 等敏感信息。

<details>
<summary>config.yaml 示例</summary>

```yaml
# 共享 auth token（空 = 不验证）
token: "your-secret-token"

# Status Service (FastAPI)
service:
  host: "0.0.0.0"
  port: 7890
  heartbeat_timeout: 30
  check_interval: 5

# tmux Poller
poller:
  service_url: "http://localhost:7890"
  poll_interval: 3
  heartbeat_interval: 10
  discover_interval: 30

# TUI Dashboard
tui:
  urls:
    - "http://localhost:7890"
    - "http://192.168.1.100:7890"   # 远程源
  machines:                          # IP → 友好名称
    "192.168.1.100": "remote-box"
  agent_machine_ips:                 # 自动发现（urls 为空时生效）
    - "192.168.1.100"

# MCP Server
mcp:
  service_url: "http://localhost:7890"
  heartbeat_interval: 10

# Web Dashboard（nginx 反代路径）
web:
  sources:
    - name: "local"
      path: "/api/local"
```
</details>

> **向后兼容**：环境变量 `AGENT_STATUS_TOKEN`、`AGENT_STATUS_URL`、`AGENT_STATUS_URLS`、`POLL_INTERVAL` 仍然可用，优先级高于 config.yaml。

### 启动（推荐用 tmux）

```bash
# 1. 启动 Status Service
tmux new-session -d -s status './start-service.sh'

# 2. 启动 tmux Poller（自动发现 Copilot CLI session）
tmux new-session -d -s poller './start-poller.sh'

# 3. 启动 TUI（在你能看到的窗口）
./start-tui.sh

# 4. 正常使用 Copilot CLI（在任意 tmux session）
# Poller 会自动发现并监控
```

**启动顺序**：Status Service → Poller → TUI。Copilot CLI 可以在任何时候启动。

---

## 服务组件

### 1. Status Service（状态聚合服务）

**文件**：`status_service/app.py` · **启动脚本**：`start-service.sh`

接收来自 Poller 的状态更新和心跳，内存存储所有 Agent 的当前状态，通过 SSE 实时推送给 TUI / Web UI。

#### API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST /status` | 接收状态更新 | `{agent_id, host, status, intent, name, autopilot}` |
| `POST /heartbeat` | 接收心跳保活 | `{agent_id, host}` |
| `POST /disconnect` | Agent 离线通知 | `{agent_id}` |
| `GET /status` | 查询所有 Agent 快照 | 返回 JSON |
| `GET /events` | SSE 实时推送 | 可选 `?agent_id=xxx` 过滤 |
| `GET /web/config` | Web UI 配置 | 从 config.yaml 返回 sources 和 token |
| `GET /web` | 提供 Web 面板 | HTML |
| `DELETE /agents/offline` | 清除所有离线 Agent | — |

#### 状态机

```
working ──(30s 无更新)──▶ idle
waiting ──(30s 无更新)──▶ idle
任何状态 ──(30s 无心跳)──▶ offline
```

#### 配置项

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `token` | `""` | Bearer token 认证（空 = 不验证） |
| `service.host` | `"0.0.0.0"` | 监听地址 |
| `service.port` | `7890` | 监听端口 |
| `service.heartbeat_timeout` | `30` | 心跳超时秒数 |
| `service.check_interval` | `5` | 超时检查间隔秒数 |

---

### 2. tmux Poller（屏幕轮询器）

**文件**：`tmux_poller/poller.py` · **启动脚本**：`start-poller.sh`

定期执行 `tmux capture-pane` 抓取 Copilot CLI 屏幕内容，通过正则匹配底栏 UI 模式判断当前状态。支持自动发现、自动提取 Agent 名称和 intent 文本。

#### 状态检测规则

| 优先级 | 检测目标 | 匹配模式 | 判定状态 |
|--------|---------|---------|---------|
| 1（最高） | 全屏幕 | `↑/↓ to navigate · enter to select` | `waiting` |
| 2 | 底栏 | `[●◉◎○] ... esc cancel` | `working`（+ 提取 intent） |
| 3 | 底栏 | `autopilot · / commands` | `idle`（autopilot 模式） |
| 4 | 底栏 | `/ commands · ? help` 或 `@ files · # issues` | `idle` |
| 5 | — | tmux capture 失败 | `offline` |

#### Intent 和 Agent 名称提取

```
● Exploring agent-status project esc cancel    tavern-optimizer · Claude Opus 4.6
  ↑                              ↑              ↑
  spinner   intent="Exploring agent-status project"   name="tavern-optimizer"
```

#### 命令行参数

```bash
# 自动发现模式（默认）
python3 poller.py

# 指定要监控的 tmux session
python3 poller.py test-6 tavern-3

# 强制自动发现 + 自定义间隔
python3 poller.py --auto --interval 5
```

#### 配置项

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `poller.service_url` | `http://localhost:7890` | Status Service 地址 |
| `poller.poll_interval` | `3` | 轮询间隔（秒） |
| `poller.heartbeat_interval` | `10` | 心跳间隔（秒） |
| `poller.discover_interval` | `30` | 自动发现间隔（秒） |

#### 自动发现机制

每 30 秒扫描一次所有 tmux session，通过以下特征识别 Copilot CLI：
- 屏幕包含 `Copilot v`（启动欢迎界面）
- 屏幕包含 `· Claude`（模型名称）
- 底栏包含 `/ commands · ? help`（空闲状态）或 `esc cancel`（工作状态）

新发现的 session 自动加入监控，消失的 session 自动标记 offline。

---

### 3. TUI（终端状态面板）

**文件**：`tui/app.py` · **启动脚本**：`start-tui.sh`

通过 SSE 订阅 Status Service，实时刷新状态显示。

```
⭘                       Agent Status                    21:10:31
 ● tavern-optimizer                           1m30s  ♥ 2s
 ● code-helper  ⚡autopilot                    45s  ♥ 1s
 ● reviewer     Exploring codebase            12s   ♥ 2s

 q Quit  r Refresh  d Del Offline                       ^p palette
```

**快捷键**：`q` 退出 · `r` 刷新 · `d` 删除离线

#### 多源订阅

TUI 支持同时连接多个 Status Service，在 `config.yaml` 中配置：

```yaml
tui:
  urls:
    - "http://localhost:7890"
    - "http://100.x.x.x:7890"    # 远程 Tailscale IP
  machines:
    "100.x.x.x": "remote-server"  # IP → 友好名称
```

多源模式下，每个 Agent 行会显示来源标签。每个源独立 SSE 连接，某个源断线不影响其他源。

#### 配置项

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `tui.urls` | `["http://localhost:7890"]` | Status Service 地址列表 |
| `tui.machines` | `{}` | IP → 友好名称映射 |
| `tui.agent_machine_ips` | `[]` | 自动发现用的机器 IP 列表 |

---

### 4. Web UI（浏览器状态面板）

**文件**：`web/index.html` · **nginx 配置**：`web/nginx-agent-status.conf`

纯前端单页面，通过 SSE 实时订阅多个 Status Service。与 TUI 功能对等，响应式设计支持手机查看。启动时自动从 `/web/config` 加载配置。

#### 多源配置

在 `config.yaml` 中配置：

```yaml
web:
  sources:
    - name: "local"
      path: "/api/local"
    - name: "remote"
      path: "/api/remote"
```

每增加一个来源，同步在 `web/nginx-agent-status.conf` 中添加对应的 `location /api/<name>/` 反代块。

#### 部署

```bash
sudo ln -sf $(pwd)/web/nginx-agent-status.conf /etc/nginx/sites-enabled/agent-status
sudo nginx -t && sudo systemctl reload nginx
```

---

## 多机器部署

```
┌─── Server A ──────────────┐  ┌─── Server B ──────────────┐
│ Copilot CLI (tmux)         │  │ Copilot CLI (tmux)         │
│ Poller → Status Service    │  │ Poller → Status Service    │
│          :7890              │  │          :7890              │
└────────────┬───────────────┘  └────────────┬───────────────┘
             │ SSE                           │ SSE
             └──────────┬───────────────────┘
                        ▼
              ┌── 任意一台 ──────┐
              │  TUI / Web UI    │
              │  (多源聚合显示)  │
              └─────────────────┘
```

每台服务器各自运行 Status Service + Poller，TUI / Web UI 同时订阅所有 Status Service。

---

## 故障排查

| 问题 | 排查方法 |
|------|---------|
| Poller 没有发现 session | `tmux list-sessions` 确认存在，手动 `tmux capture-pane -t <name> -p` 检查特征 |
| TUI 不更新 | `curl -s localhost:7890/status` 检查 Service，`curl -s localhost:7890/events` 检查 SSE |
| 状态一直 offline | 检查 Poller 和 Service 是否在运行 |
| 旧条目残留 | TUI 按 `d` 或 `curl -X DELETE localhost:7890/agents/offline` |

---

## 目录结构

```
agent-status/
├── config.py               # 统一配置加载器（YAML → 环境变量 → 默认值）
├── config.example.yaml     # 配置模板（复制为 config.yaml 使用）
├── start-service.sh        # 启动 Status Service
├── start-tui.sh            # 启动 TUI
├── start-poller.sh         # 启动 tmux Poller
├── requirements.txt        # Python 依赖
├── status_service/
│   └── app.py              # FastAPI 状态聚合服务
├── tmux_poller/
│   └── poller.py           # tmux 屏幕轮询器
├── tui/
│   └── app.py              # Textual 终端状态面板
└── web/
    ├── index.html           # 浏览器状态面板
    └── nginx-agent-status.conf  # nginx 反代配置
```

## License

MIT
