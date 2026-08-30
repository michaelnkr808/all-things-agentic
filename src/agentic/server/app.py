"""FastAPI application factory (provisional — Malik).

PROVISIONAL backend in front of the pipeline:

    AGENTIC_JWT_SECRET=dev-secret \
      .venv/bin/uvicorn agentic.server.app:create_app --factory

Routes:
- POST /api/login  {username, password} -> {access_token, role, ...}
- POST /api/run    Bearer token, {prompt} -> text/event-stream
                   events: run_started, gatherer_result, gathered,
                   synthesized, veto, revising, run_state, error
- POST /api/compare        Bearer token -> the same prompt under two roles
- GET  /api/replays        recorded runs available to this role
- POST /api/replay/{name}  Bearer token -> the same event stream, from disk
- GET  /           reference frontend (static/index.html — Michael replaces)
- /graphs/<file>   per-run rendered graph viz pages
- /replays/<file>  graph pages saved beside a recording

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

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from agentic import audit
from agentic.client import viz
from agentic.contracts.config import ConfigError
from agentic.env import load_env
from agentic.gatherers import permissions
from agentic.server import auth, compare, replay, runs
from agentic.server.schemas import (
    CompareRequest,
    FleetResponse,
    LoginRequest,
    RunRequest,
    TokenResponse,
)
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


def _recordings_dir() -> Path:
    return Path(_env_path("AGENTIC_RECORDINGS_DIR", str(replay.RECORDINGS_DIR)))


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

    # Per-IP login failure limiter, one per app instance. Per-process is
    # acceptable at the current single-instance shape; see LoginRateLimiter.
    login_limiter = auth.LoginRateLimiter()

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/api/demo-mode")
    def demo_mode() -> dict:
        """Demo identity autofill, strictly opt-in.

        Returns credentials only when AGENTIC_DEMO_MODE=1 AND the operator
        put `demo_password` entries in users.yaml. Off by default so a
        deployed instance never serves passwords to view-source.
        """
        if os.environ.get("AGENTIC_DEMO_MODE") != "1":
            return {"enabled": False, "identities": []}
        try:
            users_cfg = auth.load_users(
                _env_path("AGENTIC_USERS_PATH", auth.DEFAULT_USERS_CONFIG)
            )
        except ConfigError as e:
            raise HTTPException(503, detail=str(e)) from None
        identities = [
            {
                "username": name,
                "role": entry.role,
                "password": entry.demo_password,
            }
            for name, entry in users_cfg.users.items()
            if entry.demo_password
        ]
        return {"enabled": True, "identities": identities}

    @app.post("/api/login", response_model=TokenResponse)
    def login(body: LoginRequest, request: Request) -> TokenResponse:
        client_ip = request.client.host if request.client else "unknown"
        if not login_limiter.allow(client_ip):
            raise HTTPException(
                429, detail="too many failed logins; try again shortly"
            )

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
            login_limiter.record_failure(client_ip)
            raise HTTPException(401, detail="invalid credentials") from None
        except auth.UnknownRole as e:
            login_limiter.record_failure(client_ip)
            raise HTTPException(403, detail=str(e)) from None

        login_limiter.record_success(client_ip)
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

        # Off unless an operator asks for it: recording writes this run's
        # gathered file contents to disk, which is a deliberate act, not a
        # default. The recorder wraps emit, so what lands on disk is exactly
        # what went to the browser.
        recorder = None
        if os.environ.get("AGENTIC_RECORD") == "1":
            recordings = _recordings_dir()
            recorder = replay.Recorder(
                prompt=body.prompt,
                role=principal.role,
                name=replay.unique_name(body.prompt, principal.role, recordings),
            )
            run_emit = recorder.wrap(emit)
        else:
            run_emit = emit

        async def runner() -> None:
            try:
                # Every permission decision inside this block is attributed to
                # this run and this principal — see agentic/audit.py.
                with audit.run_context(
                    run_id=run_id,
                    principal=principal.username,
                    role=principal.role,
                    prompt=body.prompt,
                ):
                    await runs.execute_run(
                        body.prompt,
                        principal.role,
                        emit=run_emit,
                        config_path=config_path,
                        config=config,
                    )
            except asyncio.CancelledError:
                sse.emit("error", {"message": "run cancelled"})
                raise
            except Exception:
                pass  # execute_run already emitted `error`; nothing left to stream
            finally:
                if recorder is not None and recorder.events:
                    # Best effort: a failed save must not take down the run
                    # that already streamed successfully.
                    try:
                        recorder.save(_recordings_dir())
                    except OSError:
                        pass
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

    @app.post("/api/compare")
    async def compare_roles_run(
        body: CompareRequest,
        principal: auth.TokenPrincipal = Depends(current_principal),
    ) -> StreamingResponse:
        """Run one prompt as the caller and as a strictly narrower role.

        Downward only. `as_role` must see fewer departments than the caller,
        checked before anything runs — otherwise this endpoint would be a way
        to ask "what would an admin see?" from an analyst's session. Both
        sides still pass every gate under their own role; this runs two
        permission checks, it does not skip one.
        """
        config_path = _env_path("AGENTIC_CONFIG_PATH", runs.DEFAULT_CONFIG_PATH)
        try:
            perms = runs.permissions_for_request(principal.role)
            compare.check_comparable(principal.role, body.as_role, perms)
            config = runs.apply_overrides(
                runs.load_config(config_path),
                max_gatherers=body.max_gatherers,
                max_retries=body.max_retries,
                veto_model=body.veto_model,
                departments=body.departments,
            )
        except compare.CompareError as e:
            raise HTTPException(403, detail=str(e)) from None
        except runs.OverrideError as e:
            raise HTTPException(400, detail=str(e)) from None
        except ConfigError as e:
            raise HTTPException(503, detail=str(e)) from None

        sse = SseQueue()
        run_id = uuid.uuid4().hex

        def emit(event: str, payload: dict) -> None:
            if event == "compare_started":
                payload = {**payload, "run_id": run_id}
            sse.emit(event, payload)

        async def execute(prompt: str, role: str, *, emit, **kwargs) -> dict:
            # Each side carries its own role into the ledger, so a comparison
            # leaves two attributable halves rather than one blurred record.
            with audit.run_context(
                run_id=run_id,
                principal=principal.username,
                role=role,
                prompt=prompt,
            ):
                return await runs.execute_run(
                    prompt, role, emit=emit, config_path=config_path, config=config
                )

        async def runner() -> None:
            try:
                await compare.run_comparison(
                    body.prompt,
                    principal.role,
                    body.as_role,
                    execute=execute,
                    emit=emit,
                )
            except asyncio.CancelledError:
                sse.emit("error", {"message": "comparison cancelled"})
                raise
            except Exception:
                pass  # run_comparison already emitted the per-side error
            finally:
                _active_runs.pop(run_id, None)
                sse.close()

        task = asyncio.create_task(runner())
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)
        _active_runs[run_id] = (task, principal.username)
        return StreamingResponse(
            sse.stream(),
            media_type="text/event-stream",
            headers=_SSE_HEADERS,
        )

    @app.get("/api/audit")
    def audit_log(
        limit: int = 200,
        principal: auth.TokenPrincipal = Depends(current_principal),
    ) -> dict:
        """This principal's permission decisions, newest first.

        Pinned to the caller: an audit line names files inside departments the
        reader may not be cleared for, so one user's ledger is not another's
        to browse. The denials in here are the caller's own, which is exactly
        what makes them worth showing.
        """
        entries = audit.read(principal=principal.username, limit=max(1, min(limit, 1000)))
        return {"entries": entries, "summary": audit.summarise(entries)}

    @app.get("/api/replays")
    def replays(
        principal: auth.TokenPrincipal = Depends(current_principal),
    ) -> dict:
        """Recorded runs, with the role each one needs.

        Everything is listed, not just this principal's — knowing that an
        admin recording exists is harmless, and hiding it would make the
        role pin look like a bug rather than the point. The contents stay
        behind the check in /api/replay.
        """
        available = replay.list_recordings(_recordings_dir())
        for item in available:
            item["playable"] = item["role"] == principal.role
        return {"replays": available, "role": principal.role}

    @app.post("/api/replay/{name}")
    async def replay_run(
        name: str,
        speed: float = 1.0,
        principal: auth.TokenPrincipal = Depends(current_principal),
    ) -> StreamingResponse:
        """Stream a recorded run back. No model calls, no filesystem reads.

        The role pin is the whole security story here: a recording holds the
        files the recording principal was allowed to read, already past every
        gate. Replaying an admin recording for an analyst would hand over
        exactly what the gates spent the run refusing, so a mismatch is a
        403 before a single frame is written.
        """
        try:
            data = replay.load_recording(name, _recordings_dir())
        except replay.ReplayError as e:
            raise HTTPException(404, detail=str(e)) from None

        if data.get("role") != principal.role:
            raise HTTPException(
                403,
                detail=(
                    f"this recording was made as {data.get('role')!r}; "
                    f"log in as {data.get('role')!r} to replay it"
                ),
            )

        sse = SseQueue()
        run_id = uuid.uuid4().hex
        recordings = _recordings_dir()

        async def player() -> None:
            try:
                await replay.stream_recording(
                    name, sse.emit, recordings, speed=speed, run_id=run_id
                )
            except asyncio.CancelledError:
                sse.emit("error", {"message": "replay cancelled"})
                raise
            except replay.ReplayError as e:
                sse.emit("error", {"message": str(e)})
            finally:
                _active_runs.pop(run_id, None)
                sse.close()

        task = asyncio.create_task(player())
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)
        # Registered like a live run so /cancel works on a replay unchanged.
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

    # Only the saved graph pages are mounted. The .json recordings sit one
    # level up and stay off the static surface entirely: they hold the files
    # the recording principal was cleared to read, and /api/replay is the only
    # way to them precisely so the role check cannot be skipped by asking for
    # the file directly.
    replay_pages = replay.pages_dir(_recordings_dir())
    replay_pages.mkdir(parents=True, exist_ok=True)
    app.mount("/replays", StaticFiles(directory=replay_pages), name="replays")

    graphs_dir = Path(_env_path("AGENTIC_GRAPHS_DIR", str(runs.GRAPHS_DIR)))
    graphs_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/graphs", StaticFiles(directory=graphs_dir), name="graphs")

    return app


app = create_app()
