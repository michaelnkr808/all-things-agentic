# Cartographer — startup guide

From clean clone to a live run in the browser. For the auth/security model see
[AUTH_SECURITY.md](AUTH_SECURITY.md); architecture and ownership live in
[PLAN.md](../PLAN.md).

## Prerequisites

- Python 3.11+
- API keys: `GOOGLE_API_KEY` (Gemini — state manager + gatherers) and
  `ANTHROPIC_API_KEY` (Claude — client + veto checker)
- **Free-tier warning:** Gemini's free tier is ~20 requests/day/model and one
  run costs 5–8 calls → roughly 3 runs/day per model. Billing or Vertex AI
  needed for real demos.

## 1. Install

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## 2. Config files

```bash
cp config/environment.example.yaml config/environment.yaml    # departments, models, limits
cp config/permissions.example.yaml config/permissions.yaml    # roles -> departments
cp config/users.example.yaml      config/users.yaml           # login accounts
```

All three are gitignored. Then set real passwords in `users.yaml`:

```bash
python scripts/hash_password.py     # prompts securely; paste output as password_hash:
```

## 3. Environment variables

Put these in `.env` at the repo root (loaded automatically by
`agentic.env.load_env()`) or export them:

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `AGENTIC_JWT_SECRET` | **yes** | — | Token signing. Server refuses to start without it |
| `GOOGLE_API_KEY` | yes (models) | — | Gemini |
| `ANTHROPIC_API_KEY` | yes (models) | — | Claude |
| `AGENTIC_JWT_TTL_HOURS` | no | `12` | Token lifetime |
| `AGENTIC_USERS_PATH` | no | `config/users.yaml` | User store location |
| `AGENTIC_CONFIG_PATH` | no | `config/environment.yaml` | Fleet config |
| `AGENTIC_GRAPHS_DIR` | no | `out/graphs` | Rendered viz pages |
| `AGENTIC_DEMO_MODE` | no | off | `=1` enables one-click demo identities |
| `AGENTIC_DEV_CORS` | no | off | `=1` allows all origins — dev only |

Generate a secret:

```bash
export AGENTIC_JWT_SECRET=$(python -c "import secrets;print(secrets.token_hex(32))")
```

## 4. Run

```bash
.venv/bin/uvicorn agentic.server.app:create_app --factory --port 8000
```

Open <http://localhost:8000>.

### Optional: demo identities

For local demos you can enable one-click login buttons on the sign-in card.
Add `demo_password:` to each user in `config/users.yaml` (must equal that
user's real password), then start the server with `AGENTIC_DEMO_MODE=1`.
Without the flag the demo section never appears and no credentials are ever
served — see [AUTH_SECURITY.md](AUTH_SECURITY.md).

## 5. Use the app

1. **Sign in** with a `users.yaml` account (e.g. `alice`, analyst).
2. The left rail shows your fleet: departments you may read vs locked ones,
   storage providers, models, gatherer limits.
3. **Ask** something cross-department, e.g. *"Compare Q3 engineering spend to
   the finance budget"*, and hit Run. Watch the stage rail: plan → gather →
   synthesize → veto, with per-file events streaming into the graph.
4. Denied files stay visible on the map, locked — that is the permission gate
   working, not a bug.
5. When the veto checker rejects an answer it streams `revising` and retries,
   capped by `max_retries`; the final `run_state` carries verdict + attempts.
6. **Cancel** stops your own in-flight run (`run_started` gives you the
   `run_id`; only you can cancel it).
7. Open the standalone graph from the answer pane link.

### Demo beat: permissions

Log in as `alice` (analyst) and ask about HR compensation bands — watch the
denials stream live. Switch user to `root` (admin, demo mode only) and ask
the same question: HR unlocks, same prompt. That difference is the product.

## CLI alternative (no server)

```bash
.venv/bin/python -m agentic.pipeline "Summarise Q3 engineering spend"
```

Writes obsidian-flavored markdown + a self-contained graph HTML to `out/`.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `503 ... AGENTIC_JWT_SECRET is not set` | Expected fail-closed behavior. Export the secret (step 3) |
| `401 invalid credentials` on login | Wrong password, or role missing from `permissions.yaml` roles (that variant returns 403). Re-check `users.yaml` hash matches the password |
| `429 too many failed logins` | Rate limiter tripped: 5 failures/min/IP. Wait a minute or restart the server (buckets are per-process) |
| `QuotaExhausted` mid-run | Daily Gemini quota gone — not retried by design. Enable billing/Vertex or wait for reset |
| Live tests skip in pytest | No API keys loaded — check `.env` sits at repo root and keys are correct |
| Frontend served separately (vite) can't call the API | Start backend with `AGENTIC_DEV_CORS=1` (dev machines only) |
