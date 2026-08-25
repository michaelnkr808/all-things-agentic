"""FastAPI application factory (provisional — Malik).

PROVISIONAL backend in front of the pipeline:

    AGENTIC_JWT_SECRET=dev-secret \
      .venv/bin/uvicorn agentic.server.app:create_app --factory

Routes:
- POST /api/login  {username, password} -> {access_token, role, ...}
- POST /api/run    Bearer token, {prompt} -> text/event-stream
                   events: run_started, gatherer_result, gathered,
                   synthesized, veto, revising, run_state, error
- GET  /           reference frontend (static/index.html — Michael replaces)
- /graphs/<file>   per-run rendered graph viz pages

Statelessness notes: no sessions, no server-side token store — the JWT
carries {sub, role}. The authenticated role threads into every permission
gate as requester_role; it is never accepted from request bodies.

Config paths are operator-controlled environment variables, never request
fields (a client-supplied path would be an arbitrary-file-read hole).
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from agentic.contracts.config import ConfigError
from agentic.gatherers import permissions
from agentic.server import auth, runs
from agentic.server.schemas import LoginRequest, RunRequest, TokenResponse
from agentic.server.sse import SseQueue

STATIC_DIR = Path(__file__).parent / "static"

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",  # nginx must not buffer the stream
}

_background_tasks: set[asyncio.Task] = set()


def _env_path(env_var: str, default: str) -> str:
    return os.environ.get(env_var, default)


async def current_principal(
    authorization: str | None = Header(default=None),
) -> auth.TokenPrincipal:
    """FastAPI dependency: verify the Bearer token or 401."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, detail="missing bearer token")
    try:
        secret = auth.load_secret()
    except ConfigError as e:
        raise HTTPException(503, detail=str(e)) from None
    try:
        return auth.verify_token(authorization.removeprefix("Bearer ").strip(), secret)
    except auth.InvalidToken as e:
        raise HTTPException(401, detail=f"invalid token ({e})") from None


def create_app() -> FastAPI:
    app = FastAPI(
        title="all-things-agentic backend",
        version="0.1.0",
        description="SSE backend over the enterprise agent fleet (provisional).",
    )

    if os.environ.get("AGENTIC_DEV_CORS") == "1":
        # Dev only: lets a separately-served frontend (vite etc.) call the API.
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.post("/api/login", response_model=TokenResponse)
    def login(body: LoginRequest) -> TokenResponse:
        try:
            users_cfg = auth.load_users(
                _env_path("AGENTIC_USERS_PATH", auth.DEFAULT_USERS_CONFIG)
            )
            perms = permissions.load_permissions_config()
            secret = auth.load_secret()
        except ConfigError as e:
            raise HTTPException(503, detail=str(e)) from None

        try:
            principal = auth.authenticate(
                users_cfg, body.username, body.password, known_roles=set(perms.roles)
            )
        except auth.InvalidCredentials:
            raise HTTPException(401, detail="invalid credentials") from None
        except auth.UnknownRole as e:
            raise HTTPException(403, detail=str(e)) from None

        return TokenResponse(
            access_token=auth.issue_token(principal, secret),
            username=principal.username,
            role=principal.role,
        )

    @app.post("/api/run")
    async def run(
        body: RunRequest,
        principal: auth.TokenPrincipal = Depends(current_principal),
    ) -> StreamingResponse:
        sse = SseQueue()

        async def runner() -> None:
            try:
                await runs.execute_run(
                    body.prompt,
                    principal.role,
                    emit=sse.emit,
                    config_path=_env_path("AGENTIC_CONFIG_PATH", runs.DEFAULT_CONFIG_PATH),
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                pass  # execute_run already emitted `error`; nothing left to stream
            finally:
                sse.close()

        # Referenced so the running task is never GC'd mid-stream.
        task = asyncio.create_task(runner())
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)
        return StreamingResponse(
            sse.stream(),
            media_type="text/event-stream",
            headers=_SSE_HEADERS,
        )

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

        @app.get("/", include_in_schema=False)
        def index() -> FileResponse:
            return FileResponse(STATIC_DIR / "index.html")

    graphs_dir = Path(_env_path("AGENTIC_GRAPHS_DIR", str(runs.GRAPHS_DIR)))
    graphs_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/graphs", StaticFiles(directory=graphs_dir), name="graphs")

    return app


app = create_app()
