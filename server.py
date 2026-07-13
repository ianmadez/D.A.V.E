"""
DAVE Server — FastAPI backend for the local web application.

Complete implementation with WebSocket streaming, API routes,
and engine lifecycle management.
"""

import os
import json
import queue
import threading
import secrets
import time
import asyncio
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from engine import DAVEEngine, get_file_tree
from dotenv import load_dotenv

# ── Load environment ──────────────────────────────────────────────
load_dotenv()

# ── Configuration ─────────────────────────────────────────────────
DAVE_PASSPHRASE = os.getenv("DAVE_PASSPHRASE", "dave-local")
HOST = os.getenv("DAVE_HOST", "127.0.0.1")
PORT = int(os.getenv("DAVE_PORT", "8000"))
FRONTEND_DIR = Path(__file__).parent / "frontend"

engine: DAVEEngine = None

# ── Session store ─────────────────────────────────────────────────
_active_sessions: dict = {}
_session_lock = threading.Lock()


def _generate_token() -> str:
    return secrets.token_hex(32)


def _validate_session(request: Request) -> bool:
    token = request.cookies.get("dave_session")
    if token and token in _active_sessions:
        return True
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer ") and auth[7:] in _active_sessions:
        return True
    return False


def _validate_token(token: str) -> bool:
    return token in _active_sessions


# ── WebSocket broadcast infrastructure ──────────────────────────

_ws_queue: queue.Queue = queue.Queue()
_active_websockets: set[WebSocket] = set()
_broadcaster_task: asyncio.Task = None


def _ws_dispatcher(msg):
    """Called by engine consumer thread. Pushes msg into thread-safe
    queue for the async broadcaster."""
    _ws_queue.put(msg)


async def _ws_broadcaster():
    """Async task: polls _ws_queue, broadcasts to all WebSocket clients."""
    loop = asyncio.get_event_loop()
    while True:
        msg = await loop.run_in_executor(None, _ws_queue.get)
        if msg is None:
            break
        msg_type = msg[0] if len(msg) > 0 else "unknown"
        data = msg[1] if len(msg) > 1 else None
        color = msg[2] if len(msg) > 2 else "white"
        payload = json.dumps({"type": msg_type, "data": data, "color": color})
        dead = set()
        for ws in _active_websockets:
            try:
                await ws.send_text(payload)
            except Exception:
                dead.add(ws)
        _active_websockets -= dead


# ── Lifespan ──────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    global engine, _broadcaster_task
    engine = DAVEEngine(on_message_callback=_ws_dispatcher)
    _broadcaster_task = asyncio.create_task(_ws_broadcaster())
    print(
        f"DAVE Engine initialized. Passphrase: "
        f"{'SET' if DAVE_PASSPHRASE != 'dave-local' else 'DEFAULT (dave-local)'}"
    )
    yield
    _ws_queue.put(None)
    if _broadcaster_task:
        _broadcaster_task.cancel()
    print("DAVE Server shutting down.")


# ── FastAPI App ───────────────────────────────────────────────────

app = FastAPI(
    title="D.A.V.E. — Direct Agentic Versioning Engine",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ════════════════════════════════════════════════════════════════════
#  STATIC FILES
# ════════════════════════════════════════════════════════════════════

if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


@app.get("/")
async def serve_index():
    """Serve the SPA dashboard with automatic cache-busting timestamp."""
    index_path = FRONTEND_DIR / "index.html"
    if index_path.exists():
        html = index_path.read_text(encoding="utf-8")
        cache_buster = str(int(time.time()))
        html = html.replace("{{ cache_buster }}", cache_buster)
        return HTMLResponse(content=html)
    return HTMLResponse("<h1>D.A.V.E. Server Running</h1>")


# ═══════════════════════════════════════════════════════════════════
#  AUTH
# ═══════════════════════════════════════════════════════════════════

@app.post("/api/login")
async def api_login(request: Request):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body.")
    passphrase = body.get("passphrase", "")
    if not passphrase:
        raise HTTPException(status_code=400, detail="Missing passphrase.")
    if passphrase != DAVE_PASSPHRASE:
        raise HTTPException(status_code=401, detail="Invalid passphrase.")
    token = _generate_token()
    with _session_lock:
        _active_sessions[token] = True

    response = JSONResponse({
        "success": True, "token": token,
        "message": "Authenticated successfully.",
    })
    response.set_cookie(
        key="dave_session", value=token,
        httponly=True, samesite="lax", max_age=86400)
    return response


# ═══════════════════════════════════════════════════════════════════
#  WEBSOCKET — Real-time agent stream
# ═══════════════════════════════════════════════════════════════════

@app.websocket("/ws/agent")
async def agent_websocket(websocket: WebSocket):
    """Bi-directional WebSocket for real-time agent streaming.

    Client sends:
        {"type":"auth","token":"..."}
        {"type":"send_message","message":"..."}
        {"type":"stop"}
        {"type":"undo"}
        {"type":"settings","mode":"agent",...}

    Server broadcasts all engine queue messages as JSON.
    """
    await websocket.accept()
    authenticated = False
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue

            msg_type = data.get("type", "")

            if msg_type == "auth":
                token = data.get("token", "")
                if _validate_token(token):
                    authenticated = True
                    _active_websockets.add(websocket)
                    await websocket.send_text(json.dumps({
                        "type": "status", "data": "Connected", "color": "green"}))
                    # If a workspace path is already loaded in our global engine singleton, sync it immediately
                    if engine and engine.target_directory:
                        await websocket.send_text(json.dumps({
                            "type": "workspace_refreshed", "data": "", "color": "white"}))
                else:
                    await websocket.send_text(json.dumps({
                        "type": "status", "data": "Auth failed", "color": "red"}))
                    await websocket.close()
                    return
                continue

            if not authenticated:
                await websocket.send_text(json.dumps({
                    "type": "status", "data": "Authenticate first", "color": "red"}))
                continue

            if msg_type == "send_message":
                message = data.get("message", "")
                if message:
                    engine.send_message(message)
            elif msg_type == "stop":
                engine.stop_agent()
            elif msg_type == "undo":
                engine.undo_last_edit()
            elif msg_type == "settings":
                if "mode" in data and data["mode"] in ("agent", "chat"):
                    engine.mode = data["mode"]
                if "llm" in data:
                    engine.set_llm_mode(data["llm"])
                if "guided_demo" in data:
                    engine.set_demo_mode(bool(data["guided_demo"]))
                if "max_agent_turns" in data:
                    engine.max_agent_turns = int(data["max_agent_turns"])
                if "max_chat_turns" in data:
                    engine.max_chat_turns = int(data["max_chat_turns"])
            elif msg_type == "reset":
                engine.send_message("/reset")
    except WebSocketDisconnect:
        pass
    finally:
        _active_websockets.discard(websocket)


# ═══════════════════════════════════════════════════════════════════
#  SEND (HTTP fallback)
# ═══════════════════════════════════════════════════════════════════

@app.post("/api/send")
async def api_send(request: Request):
    if not _validate_session(request):
        raise HTTPException(status_code=401, detail="Unauthorized.")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON.")
    message = body.get("message", "")
    if not message:
        raise HTTPException(status_code=400, detail="Missing message.")
    task_id = engine.send_message(message)
    return {"success": True, "task_id": task_id}


# ═══════════════════════════════════════════════════════════════════
#  WORKSPACE
# ═══════════════════════════════════════════════════════════════════

@app.post("/api/workspace")
async def api_set_workspace(request: Request):
    if not _validate_session(request):
        raise HTTPException(status_code=401, detail="Unauthorized.")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON.")
    directory = body.get("path", "")
    if not directory:
        raise HTTPException(status_code=400, detail="Missing path.")
    directory = os.path.abspath(os.path.normpath(directory))
    if not os.path.isdir(directory):
        raise HTTPException(status_code=400, detail=f"Directory not found: {directory}")
    success = engine.init_workspace(directory)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to init workspace.")
    file_count = len(engine.workspace_index.get("files", {}))
    return {
        "success": True, "directory": directory,
        "file_count": file_count,
        "message": f"Workspace loaded: {os.path.basename(directory)} ({file_count} files)",
    }


# ═══════════════════════════════════════════════════════════════════
#  EXPLORER — File tree JSON
# ═══════════════════════════════════════════════════════════════════

@app.get("/api/explorer")
async def api_explorer(request: Request):
    if not _validate_session(request):
        raise HTTPException(status_code=401, detail="Unauthorized.")
    return get_file_tree(engine)


# ═══════════════════════════════════════════════════════════════════
#  STATUS
# ═══════════════════════════════════════════════════════════════════

@app.get("/api/status")
async def api_status(request: Request):
    if not _validate_session(request):
        raise HTTPException(status_code=401, detail="Unauthorized.")
    return {
        "is_processing": engine.is_processing,
        "mode": engine.mode,
        "llm_mode": engine.llm_mode,
        "phase": engine.TaskState["system_state"]["current_phase"],
        "confidence": engine.TaskState["system_state"]["confidence"],
        "retries": engine.TaskState["system_state"]["retry_count"],
        "target_directory": engine.target_directory,
        "workspace_files": len(engine.workspace_index.get("files", {})),
        "guided_demo_mode": engine.guided_demo_mode,
        "edit_history_count": len(engine.edit_history),
    }


# ═══════════════════════════════════════════════════════════════════
#  SETTINGS
# ═══════════════════════════════════════════════════════════════════

@app.post("/api/settings")
async def api_settings(request: Request):
    if not _validate_session(request):
        raise HTTPException(status_code=401, detail="Unauthorized.")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON.")
    if "mode" in body:
        m = body["mode"]
        if m not in ("agent", "chat"):
            raise HTTPException(status_code=400, detail="Mode must be agent/chat.")
        engine.mode = m
    if "llm" in body:
        engine.set_llm_mode(body["llm"])
    if "guided_demo" in body:
        engine.set_demo_mode(bool(body["guided_demo"]))
    if "max_agent_turns" in body:
        engine.max_agent_turns = int(body["max_agent_turns"])
    if "max_chat_turns" in body:
        engine.max_chat_turns = int(body["max_chat_turns"])
    return {"success": True, "message": "Settings updated."}


# ═══════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    ps = "Configured via .env" if DAVE_PASSPHRASE != "dave-local" else "DEFAULT (dave-local)"
    print(f"Starting D.A.V.E. Server on http://{HOST}:{PORT}")
    print(f"Frontend: {FRONTEND_DIR}")
    print(f"Passphrase: {ps}")
    uvicorn.run("server:app", host=HOST, port=PORT, reload=True)