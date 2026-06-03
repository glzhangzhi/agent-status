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

---

## 部署指南

### 第 1 步：安装

```bash
# 前置要求：tmux, Python 3.11+, pip
# 确认版本
tmux -V        # tmux 3.x
python3 -V     # Python 3.11+

# 克隆仓库
git clone https://github.com/glzhangzhi/agent-status.git
cd agent-status

# 安装 Python 依赖
pip install -r requirements.txt
```

### 第 2 步：配置

```bash
# 复制配置模板
cp config.example.yaml config.yaml
```

编辑 `config.yaml`。**最简配置**什么都不用改，所有默认值开箱即用：

```yaml
# 最简配置：全部留空/默认即可
token: ""        # 不设密码

service:
  port: 7890     # Status Service 端口
```

如果需要认证（推荐用于多机部署），设置 `token`：

```yaml
token: "your-secret-token"
```

> `config.yaml` 已在 `.gitignore` 中，不会被提交，可以安全放密码。

### 第 3 步：启动服务（单机）

推荐在 tmux 中启动，这样关闭终端后服务不会停止：

```bash
cd agent-status

# 1️⃣ 启动 Status Service（后台 tmux session）
tmux new-session -d -s status './start-service.sh'

# 2️⃣ 启动 tmux Poller（后台 tmux session）
tmux new-session -d -s poller './start-poller.sh'

# 3️⃣ 启动 TUI（在你能看到的窗口）
./start-tui.sh
```

> **启动顺序很重要**：Status Service 必须最先启动。Poller 和 TUI 可以随后启动。Copilot CLI 可以在任何时候启动。

### 第 4 步：验证

```bash
# ✅ 检查 Status Service 是否正常运行
curl -s http://localhost:7890/status | python3 -m json.tool
# 应输出: {"agents": {}}

# ✅ 检查 Poller 日志（看是否发现了 Copilot CLI session）
tmux attach -t poller
# 应看到: [poller] Starting ... 
# 如果有 Copilot CLI 在运行: [poller] Discovered: <session-name>
# 按 Ctrl+B D 退出 tmux（不是关闭）

# ✅ 检查 TUI 是否显示状态
tmux attach -t tui
# 应看到 Agent Status 面板，如果有 Copilot CLI 在运行会显示彩色圆点
```

如果你还没有运行 Copilot CLI，现在打开一个新 tmux session 运行它，Poller 会在 30 秒内自动发现。

---

### Web UI 部署（可选）

Web UI 让你可以从浏览器（包括手机）查看 Agent 状态。需要 nginx 做反向代理。

#### 1. 安装 nginx

```bash
sudo apt update && sudo apt install -y nginx
```

#### 2. 编辑 nginx 配置

```bash
# 复制模板
sudo cp web/nginx-agent-status.conf /etc/nginx/sites-available/agent-status

# 编辑配置
sudo nano /etc/nginx/sites-available/agent-status
```

需要修改的地方：

```nginx
server {
    listen 80;
    server_name agent-status.example.com;  # ← 改成你的域名或 IP

    root /path/to/agent-status/web;        # ← 改成 agent-status/web 的绝对路径

    # ── Local Status Service ──
    location /api/local/ {
        proxy_pass http://127.0.0.1:7890/;  # ← 确认端口和 config.yaml 一致
        # ... 其他 proxy 设置保持不变 ...
    }

    # ── 如果有远程 Status Service，取消注释并编辑 ──
    # location /api/remote/ {
    #     proxy_pass http://<remote-ip>:7890/;
    #     ...
    # }
}
```

#### 3. 启用配置并重载

```bash
# 创建软链接启用站点
sudo ln -sf /etc/nginx/sites-available/agent-status /etc/nginx/sites-enabled/

# 测试配置语法
sudo nginx -t

# 重载 nginx
sudo systemctl reload nginx
```

#### 4. 配置 Web UI 数据源

在 `config.yaml` 中添加 Web UI 的数据源（必须与 nginx 中的 `location` 路径对应）：

```yaml
web:
  sources:
    - name: "local"
      path: "/api/local"
    # - name: "remote"        # 对应 nginx 中的 location /api/remote/
    #   path: "/api/remote"
```

#### 5. 验证

```bash
# 本地访问
curl -s http://localhost/

# 或用浏览器打开
# http://<你的IP或域名>/
```

> **HTTPS**：如果需要 HTTPS，推荐使用 [Certbot](https://certbot.eff.org/) (`sudo certbot --nginx`) 或 Cloudflare 代理。

#### 6. 声音提醒

Web UI 内置了提示音功能：当任意 Agent 从 `working` 变为 `idle` 或 `waiting` 时，会播放一个双音提示音。首次加载页面时不会触发（避免一打开就响一堆）。

> **注意**：浏览器要求用户至少点击过页面一次后才允许播放声音（浏览器安全策略）。

---

### 多机部署（可选）

如果你有多台服务器都在运行 Copilot CLI，可以把所有服务器的 Agent 状态聚合到一个面板中查看。

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

#### 每台服务器上：

```bash
# 重复「第 1-3 步」，但不需要启动 TUI
git clone https://github.com/glzhangzhi/agent-status.git
cd agent-status
pip install -r requirements.txt
cp config.example.yaml config.yaml
# 编辑 config.yaml，设置相同的 token

tmux new-session -d -s status './start-service.sh'
tmux new-session -d -s poller './start-poller.sh'
```

确保各台服务器的 Status Service 端口（默认 7890）在网络中可达（防火墙放行、Tailscale/VPN 等）。

#### 在你要看面板的那台机器上：

编辑 `config.yaml`：

```yaml
tui:
  urls:
    - "http://localhost:7890"            # 本机
    - "http://192.168.1.100:7890"        # Server B
    - "http://192.168.1.101:7890"        # Server C
  machines:                              # IP → 友好名称（TUI 中显示）
    "192.168.1.100": "workstation"
    "192.168.1.101": "cloud-server"
```

然后启动 TUI：

```bash
./start-tui.sh
```

多源模式下，TUI 每行会显示来源标签，每个源独立 SSE 连接，某个源断线不影响其他源。

#### Web UI 多源

如果用 Web UI，需要在 nginx 中为每个远程 Status Service 添加一个 `location` 反代块，然后在 `config.yaml` 中配置对应的 `web.sources`：

```nginx
# nginx: 添加远程源的反代
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
# config.yaml
web:
  sources:
    - name: "local"
      path: "/api/local"
    - name: "workstation"
      path: "/api/workstation"
```

---

### 开机自启（可选）

如果希望服务在机器重启后自动启动，可以用 crontab：

```bash
crontab -e
```

添加以下内容（修改路径为你的实际路径）：

```
@reboot sleep 5 && cd /path/to/agent-status && tmux new-session -d -s status './start-service.sh'
@reboot sleep 8 && cd /path/to/agent-status && tmux new-session -d -s poller './start-poller.sh'
```

> TUI 一般不需要自启，因为需要手动查看。

---

## 使用指南

### TUI 操作

| 按键 | 功能 |
|------|------|
| `q` | 退出 TUI |
| `r` | 手动刷新（重新拉取所有 Agent 的当前状态） |
| `d` | 删除所有已离线的 Agent 条目 |

每个 Agent 显示为一行：

```
● tavern-optimizer  Exploring codebase  1m30s  ♥ 2s
↑                   ↑                   ↑      ↑
状态灯(颜色)        当前 intent         持续时间 上次心跳
```

### 常用 API 操作

```bash
# 查看所有 Agent 状态
curl -s http://localhost:7890/status | python3 -m json.tool

# 订阅 SSE 实时推送（Ctrl+C 退出）
curl -s http://localhost:7890/events

# 删除所有离线 Agent
curl -X DELETE http://localhost:7890/agents/offline

# 查看 Web UI 配置
curl -s http://localhost:7890/web/config | python3 -m json.tool
```

如果设置了 token，需要加上 `-H "Authorization: Bearer <token>"`。

### Poller 命令行

```bash
# 自动发现模式（默认，推荐）
./start-poller.sh

# 只监控指定的 tmux session
./start-poller.sh my-session-1 my-session-2

# 自定义轮询间隔
cd tmux_poller && python3 poller.py --auto --interval 5
```

---

## 配置参考

所有配置项都在 `config.yaml` 中，按组件分组：

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `token` | `""` | 共享 Bearer token（空 = 不验证） |
| **service** | | |
| `service.host` | `"0.0.0.0"` | Status Service 监听地址 |
| `service.port` | `7890` | Status Service 监听端口 |
| `service.heartbeat_timeout` | `30` | 无心跳超时秒数 → 标记 offline |
| `service.check_interval` | `5` | 超时检查间隔秒数 |
| **poller** | | |
| `poller.service_url` | `http://localhost:7890` | Status Service 地址 |
| `poller.poll_interval` | `3` | tmux 屏幕捕获间隔（秒） |
| `poller.heartbeat_interval` | `10` | 心跳发送间隔（秒） |
| `poller.discover_interval` | `30` | 自动发现扫描间隔（秒） |
| **tui** | | |
| `tui.urls` | `["http://localhost:7890"]` | Status Service 地址列表 |
| `tui.machines` | `{}` | IP → 友好名称映射（多源时显示） |
| `tui.agent_machine_ips` | `[]` | 自动发现用的机器 IP 列表 |
| **mcp** | | |
| `mcp.service_url` | `http://localhost:7890` | Status Service 地址 |
| `mcp.heartbeat_interval` | `10` | 心跳发送间隔（秒） |
| **web** | | |
| `web.sources` | `[{name:"local", path:"/api/local"}]` | Web UI 数据源（对应 nginx 反代路径） |

> **环境变量覆盖**：`AGENT_STATUS_TOKEN`、`AGENT_STATUS_URL`、`AGENT_STATUS_URLS`、`POLL_INTERVAL` 仍可用，优先级高于 config.yaml。

---

## 技术细节

### Poller 状态检测规则

| 优先级 | 检测目标 | 匹配模式 | 判定状态 |
|--------|---------|---------|---------|
| 1（最高） | 全屏幕 | `↑/↓ to navigate · enter to select` | `waiting` |
| 2 | 底栏 | `[●◉◎○] ... esc cancel` | `working`（+ 提取 intent） |
| 3 | 底栏 | `autopilot · / commands` | `idle`（autopilot 模式） |
| 4 | 底栏 | `/ commands · ? help` 或 `@ files · # issues` | `idle` |
| 5 | — | tmux capture 失败 | `offline` |

### Intent 和 Agent 名称提取

```
● Exploring agent-status project esc cancel    tavern-optimizer · Claude Opus 4.6
  ↑                              ↑              ↑
  spinner   intent="Exploring agent-status project"   name="tavern-optimizer"
```

### 自动发现机制

Poller 每 30 秒扫描一次所有 tmux session，通过以下特征识别 Copilot CLI：
- 屏幕包含 `Copilot v`（启动欢迎界面）
- 屏幕包含 `· Claude`（模型名称）
- 底栏包含 `/ commands · ? help`（空闲状态）或 `esc cancel`（工作状态）

新发现的 session 自动加入监控，消失的 session 自动标记 offline。

### Status Service API

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST /status` | 接收状态更新 | `{agent_id, host, status, intent, name, autopilot}` |
| `POST /heartbeat` | 接收心跳 | `{agent_id, host}` |
| `POST /disconnect` | Agent 离线 | `{agent_id}` |
| `GET /status` | 所有 Agent 快照 | JSON |
| `GET /events` | SSE 实时推送 | 可选 `?agent_id=xxx` 过滤 |
| `GET /web/config` | Web UI 配置 | 从 config.yaml 返回 sources 和 token |
| `GET /web` | Web 面板页面 | HTML |
| `DELETE /agents/offline` | 清除离线 Agent | — |

### 状态机

```
working ──(30s 无更新)──▶ idle
waiting ──(30s 无更新)──▶ idle
任何状态 ──(30s 无心跳)──▶ offline
```

---

## 故障排查

### Poller 没有发现 Copilot CLI session

```bash
# 检查 tmux session 列表
tmux list-sessions

# 手动检查某个 session 是否包含 Copilot CLI 特征
tmux capture-pane -t <session-name> -p | grep -E '(Copilot v|· Claude|commands · \? help|esc cancel)'
```

如果 session 名称带数字后缀（如 `test-6`），需要用完整名称。

### TUI 不更新

```bash
# 检查 Status Service 是否响应
curl -s http://localhost:7890/status

# 检查 SSE 是否正常推送（应持续输出 event 数据，Ctrl+C 退出）
curl -s http://localhost:7890/events
```

### 状态一直显示 offline

1. Poller 没有在运行 → `ps aux | grep poller.py`
2. Status Service 没有在运行 → `curl localhost:7890/status`
3. tmux session 名称不匹配 → 手动指定：`./start-poller.sh <session-name>`

### 旧的 Agent 条目残留

```bash
# TUI 中按 d 键
# 或通过 API
curl -X DELETE http://localhost:7890/agents/offline
```

### Web UI 无法加载 / SSE 断开

1. 检查 nginx 配置是否正确：`sudo nginx -t`
2. 确认 Status Service 正在运行：`curl localhost:7890/status`
3. 检查 nginx 错误日志：`sudo tail /var/log/nginx/error.log`
4. 确认 `config.yaml` 中的 `web.sources[].path` 与 nginx `location` 路径一致

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
    └── nginx-agent-status.conf  # nginx 反代配置模板
```

## License

MIT
