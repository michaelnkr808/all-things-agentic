# all-things-agentic

Enterprise agent fleet: prompt → state manager → parallel resource gatherers →
obsidian-syntax synthesis → adversarial veto checker → interactive graph viz.

**Read [PLAN.md](PLAN.md) first** — architecture, task ownership (Michael/Malik),
and the contract layer rules.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp config/environment.example.yaml config/environment.yaml
cp config/permissions.example.yaml config/permissions.yaml
export GOOGLE_API_KEY=...      # Gemini (state manager + gatherers)
export ANTHROPIC_API_KEY=...   # Claude (client + veto checker)
```

Optional backend + auth (provisional — Malik, see PLAN.md):

```bash
cp config/users.example.yaml config/users.yaml
export AGENTIC_JWT_SECRET=$(python -c "import secrets;print(secrets.token_hex(32))")
.venv/bin/uvicorn agentic.server.app:create_app --factory --port 8000
# login -> POST /api/login {username, password}; run -> POST /api/run (Bearer, SSE)
```

## Layout

```
src/agentic/
├── contracts/       # SHARED schemas — change only with both of us agreeing
├── state_manager/   # manager.py (Malik) · output.py (Michael)
├── gatherers/       # spawn.py + permissions.py (Malik) · gather.py (Michael)
├── veto/            # checker.py (Michael)
├── client/          # viz.py (Michael)
├── server/          # auth + FastAPI SSE backend (provisional — Malik)
└── pipeline.py      # end-to-end wiring + veto retry loop (Michael)
config/              # environment.yaml — departments, permissions, models, limits
                     # users.yaml — login accounts + roles (provisional — Malik)
data/departments/    # fake enterprise data for the demo
```
