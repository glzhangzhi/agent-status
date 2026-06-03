"""Agent Status Service — FastAPI + SSE."""

import asyncio
import json
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import cfg

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

# --- Config (from unified config.yaml) ---

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
TOKEN = cfg.token
HEARTBEAT_TIMEOUT = cfg.service_heartbeat_timeout
CHECK_INTERVAL = cfg.service_check_interval


# --- Models ---

class StatusUpdate(BaseModel):
    agent_id: str
    host: str = ""
    status: str  # "working" | "waiting" | "idle"
    intent: str = ""
    name: str = ""
    autopilot: bool = False


class HeartbeatPayload(BaseModel):
    agent_id: str
    host: str = ""


class DisconnectPayload(BaseModel):
    agent_id: str


# --- State ---

class AgentState:
    def __init__(self, agent_id: str, host: str, status: str, intent: str, name: str = "", autopilot: bool = False):
        self.agent_id = agent_id
        self.host = host
        self.status = status
        self.intent = intent
        self.name = name
        self.autopilot = autopilot
        self.last_heartbeat_time = time.time()
        self.online = True

    def to_dict(self):
        return {
            "agent_id": self.agent_id,
            "host": self.host,
            "status": self.status,
            "intent": self.intent,
            "name": self.name,
            "autopilot": self.autopilot,
            "last_seen": self.last_heartbeat_time,
            "online": self.online,
        }


agents: dict[str, AgentState] = {}
# SSE subscribers: list of asyncio.Queue
subscribers: list[asyncio.Queue] = []


# --- Auth ---

def verify_token(request: Request):
    if not TOKEN:
        return  # no token configured, allow all
    auth = request.headers.get("Authorization", "")
    if auth.removeprefix("Bearer ").strip() != TOKEN:
        raise HTTPException(status_code=403, detail="Invalid token")


# --- SSE broadcast ---

async def broadcast(event_data: dict):
    for q in subscribers:
        await q.put(event_data)


# --- Timeout checker background task ---

async def timeout_checker():
    while True:
        await asyncio.sleep(CHECK_INTERVAL)
        now = time.time()
        for agent in agents.values():
            if not agent.online:
                continue
            # Heartbeat timeout → offline
            if now - agent.last_heartbeat_time > HEARTBEAT_TIMEOUT:
                agent.online = False
                agent.status = "offline"
                await broadcast(agent.to_dict())


# --- Lifespan ---

@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(timeout_checker())
    yield
    task.cancel()


# --- App ---

app = FastAPI(lifespan=lifespan)


@app.post("/status")
async def update_status(payload: StatusUpdate, request: Request):
    verify_token(request)
    agent = agents.get(payload.agent_id)
    if agent is None:
        # New agent — check if there's an offline agent with same name+host to replace
        if payload.name and payload.host:
            stale_ids = [
                aid for aid, a in agents.items()
                if not a.online and a.name == payload.name and a.host == payload.host
            ]
            for stale_id in stale_ids:
                del agents[stale_id]
                await broadcast({"agent_id": stale_id, "event": "removed"})
        agent = AgentState(payload.agent_id, payload.host, payload.status, payload.intent, payload.name, payload.autopilot)
        agents[payload.agent_id] = agent
    else:
        agent.status = payload.status
        agent.intent = payload.intent
        agent.autopilot = payload.autopilot
        agent.host = payload.host or agent.host
        if payload.name:
            agent.name = payload.name
        agent.last_heartbeat_time = time.time()
        agent.online = True
    await broadcast(agent.to_dict())
    return {"ok": True}


@app.post("/heartbeat")
async def heartbeat(payload: HeartbeatPayload, request: Request):
    verify_token(request)
    agent = agents.get(payload.agent_id)
    if agent is None:
        # First heartbeat before any status — register as idle
        agent = AgentState(payload.agent_id, payload.host, "idle", "")
        agents[payload.agent_id] = agent
    agent.last_heartbeat_time = time.time()
    agent.host = payload.host or agent.host
    if not agent.online:
        agent.online = True
        agent.status = "idle"
    await broadcast(agent.to_dict())
    return {"ok": True}


@app.post("/disconnect")
async def disconnect(payload: DisconnectPayload, request: Request):
    verify_token(request)
    agent = agents.get(payload.agent_id)
    if agent:
        agent.online = False
        agent.status = "offline"
        await broadcast(agent.to_dict())
    return {"ok": True}


@app.delete("/agents/offline")
async def delete_offline_agents(request: Request):
    verify_token(request)
    offline_ids = [aid for aid, a in agents.items() if not a.online]
    for aid in offline_ids:
        del agents[aid]
        await broadcast({"agent_id": aid, "event": "removed"})
    return {"ok": True, "removed": len(offline_ids)}


@app.get("/status")
async def get_status(request: Request):
    verify_token(request)
    return {"agents": {aid: a.to_dict() for aid, a in agents.items()}}


@app.get("/events")
async def events(request: Request, agent_id: Optional[str] = None):
    verify_token(request)
    q: asyncio.Queue = asyncio.Queue()
    subscribers.append(q)

    async def event_generator():
        try:
            while True:
                data = await q.get()
                # Optional filter by agent_id
                if agent_id and data.get("agent_id") != agent_id:
                    continue
                yield {"event": "status", "data": _json_dumps(data)}
        except asyncio.CancelledError:
            pass
        finally:
            subscribers.remove(q)

    return EventSourceResponse(event_generator(), ping=15)


def _json_dumps(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False)


@app.get("/web/config")
async def web_config():
    """Serve web UI configuration from config.yaml."""
    return {"sources": cfg.web_sources, "token": cfg.token}


@app.get("/web")
async def web_ui():
    """Serve the web dashboard."""
    index = WEB_DIR / "index.html"
    if index.exists():
        return FileResponse(index, media_type="text/html")
    raise HTTPException(status_code=404, detail="Web UI not found")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=cfg.service_host, port=cfg.service_port)
