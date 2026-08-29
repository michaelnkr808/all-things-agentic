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
import uuid
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from agentic.client import viz
from agentic.contracts.config import ConfigError
from agentic.env import load_env
from agentic.gatherers import permissions
from agentic.server import auth, runs
from agentic.server.schemas import FleetResponse, LoginRequest, RunRequest, TokenResponse
from agentic.server.sse import SseQueue

STATIC_DIR = Path(__file__).parent / "static"
# The graph library is vendored beside viz.py; the standalone pages inline it,
# the app loads it once from here and lets the browser cache it.
VENDOR_DIR = Path(viz.__file__).parent / "vendor"
# The project page (also published via GitHub Pages from docs/). Served here
# too so the running demo can link to the story without leaving localhost.
DOCS_DIR = Path(__file__).resolve().parents[3] / "docs"

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",  # nginx must not buffer the stream
}

_background_tasks: set[asyncio.Task] = set()

#: run_id -> (task, username). Lets a client cancel its own in-flight run.
#: The username is stored so cancellation is authorized against the JWT:
#: knowing a run_id must not be enough to kill someone else's run.
_active_runs: dict[str, tuple[asyncio.Task, str]] = {}


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
    load_env()  # .env in the repo root supplies the model API keys

    app = FastAPI(
        title="Cartographer",
        version="0.1.0",
        description="SSE backend over the Cartographer enterprise agent fleet.",
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

    @app.get("/api/fleet", response_model=FleetResponse)
    def fleet(
        principal: auth.TokenPrincipal = Depends(current_principal),
    ) -> FleetResponse:
        """The fleet as this principal sees it: which departments are readable,
        which are cloud-backed, which models are wired. No model calls."""
        try:
            config = runs.load_config(
                _env_path("AGENTIC_CONFIG_PATH", runs.DEFAULT_CONFIG_PATH)
            )
            return runs.build_fleet(principal.role, config)
        except ConfigError as e:
            raise HTTPException(503, detail=str(e)) from None

    @app.post("/api/run")
    async def run(
        body: RunRequest,
        principal: auth.TokenPrincipal = Depends(current_principal),
    ) -> StreamingResponse:
        config_path = _env_path("AGENTIC_CONFIG_PATH", runs.DEFAULT_CONFIG_PATH)
        # Load and validate overrides *before* streaming: a rejected knob should
        # be a 4xx the client can show, not an error event mid-stream.
        try:
            config = runs.apply_overrides(
                runs.load_config(config_path),
                max_gatherers=body.max_gatherers,
                max_retries=body.max_retries,
                veto_model=body.veto_model,
                departments=body.departments,
            )
        except runs.OverrideError as e:
            raise HTTPException(400, detail=str(e)) from None
        except ConfigError as e:
            raise HTTPException(503, detail=str(e)) from None

        sse = SseQueue()
        run_id = uuid.uuid4().hex

        def emit(event: str, payload: dict) -> None:
            # The client learns its run_id from the first event, which is all
            # it needs to cancel — there is nothing to cancel before then.
            if event == "run_started":
                payload = {**payload, "run_id": run_id}
            sse.emit(event, payload)

        async def runner() -> None:
            try:
                await runs.execute_run(
                    body.prompt,
                    principal.role,
                    emit=emit,
                    config_path=config_path,
                    config=config,
                )
            except asyncio.CancelledError:
                sse.emit("error", {"message": "run cancelled"})
                raise
            except Exception:
                pass  # execute_run already emitted `error`; nothing left to stream
            finally:
                _active_runs.pop(run_id, None)
                sse.close()

        # Referenced so the running task is never GC'd mid-stream.
        task = asyncio.create_task(runner())
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)
        _active_runs[run_id] = (task, principal.username)
        return StreamingResponse(
            sse.stream(),
            media_type="text/event-stream",
            headers=_SSE_HEADERS,
        )

    @app.post("/api/run/{run_id}/cancel")
    def cancel_run(
        run_id: str,
        principal: auth.TokenPrincipal = Depends(current_principal),
    ) -> dict:
        """Stop an in-flight run. Only the user who started it may cancel it."""
        entry = _active_runs.get(run_id)
        if entry is None:
            raise HTTPException(404, detail="no such run (it may have finished)")
        task, owner = entry
        if owner != principal.username:
            # Same 404 as an unknown id: whether someone else's run exists is
            # not this caller's business.
            raise HTTPException(404, detail="no such run (it may have finished)")
        task.cancel()
        return {"cancelled": run_id}

    if VENDOR_DIR.exists():
        app.mount("/vendor", StaticFiles(directory=VENDOR_DIR), name="vendor")

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

        @app.get("/", include_in_schema=False)
        def index() -> FileResponse:
            return FileResponse(STATIC_DIR / "index.html")

    if (DOCS_DIR / "index.html").is_file():

        @app.get("/about", include_in_schema=False)
        def about() -> FileResponse:
            """The project page. Self-contained, no API calls, always works."""
            return FileResponse(DOCS_DIR / "index.html")

    graphs_dir = Path(_env_path("AGENTIC_GRAPHS_DIR", str(runs.GRAPHS_DIR)))
    graphs_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/graphs", StaticFiles(directory=graphs_dir), name="graphs")

    return app


app = create_app()
