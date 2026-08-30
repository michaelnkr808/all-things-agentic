# Cartographer

Maps what an enterprise knows, and what you're allowed to know, into a graph
you can read.

**Project page:** <https://michaelnkr808.github.io/cartographer/> ·
source in [`docs/index.html`](docs/index.html), also served by the running app
at `/about`.

---

## What it does

You ask one question. A state manager breaks it into per-department gathering
tasks and fans out a fleet of gatherer agents in parallel. Every file each
gatherer touches is checked against your role *before* it is opened. What
survives is synthesized into an obsidian-flavored answer with `[[inline
citations]]`, and then an adversarial veto checker tries to reject it: for
unsupported claims, for citations that don't resolve, for departments the
config never allowed. If it vetoes, the answer is revised and re-checked.

The whole run is drawn as a graph while it happens, and the files you were
**denied** stay on the map, locked. That is the point of the project: an
enterprise fleet is only trustworthy if you can see the shape of what it was
refused, not just what it returned.

```
prompt
  │
  ├─ state manager (Gemini)        plan → one gathering task per department
  │
  ├─ gatherers (Gemini, parallel)  ┌ permission gate ┐ ← role checked before open
  │                                └ read → assess   ┘
  │
  ├─ synthesizer (Gemini)          obsidian markdown + [[dept/path]] citations
  │
  ├─ veto checker (Claude)         adversarial: tries to reject the answer
  │      └─ vetoed? → revise → re-check   (capped by veto.max_retries)
  │
  └─ graph (vis-network)           departments, files, denials, the answer
```

## The permission model

A role may read a department only if **both** configs agree, and this fails
closed on any disagreement:

| Layer | Where | What it stops |
|---|---|---|
| 1. Path check before open | `gatherers/gather.py` | A file is gated before it is read, not after |
| 2. Two configs must agree | `permissions.yaml` **AND** `environment.yaml` | One file being wrong is not enough to widen access |
| 3. Non-bypassable spawn gate | `gatherers/spawn.py` | The role is re-pinned from trusted config; a planner or a request body cannot set it |
| 4. Containment | `gatherers/gather.py` | `..`, absolute paths and escaping symlinks are rejected; cloud keys are checked against the bucket prefix |

The principal's role comes from the verified JWT and nothing else. Nothing an
LLM emits is ever trusted as an access decision: the planner's department
names are validated against a closed `Literal` built per request, so an
invented department fails schema validation before any code runs.

Try it as two identities and the difference is the product: `alice` (analyst)
asking about HR compensation gets a map of locks, `root` (admin) asking the
same question gets an answer.

## Quickstart

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

cp config/environment.example.yaml config/environment.yaml
cp config/permissions.example.yaml config/permissions.yaml
cp config/users.example.yaml      config/users.yaml
python scripts/hash_password.py     # paste the output as password_hash:
```

Put the keys in `.env` at the repo root — `agentic.env.load_env()` loads it
from every entry point, and never overrides a real environment variable:

```bash
GOOGLE_API_KEY=...            # Gemini: state manager + gatherers
ANTHROPIC_API_KEY=...         # Claude: veto checker
AGENTIC_JWT_SECRET=...        # python -c "import secrets;print(secrets.token_hex(32))"
AGENTIC_DEMO_MODE=1           # optional, local demos only
```

Then:

```bash
.venv/bin/uvicorn agentic.server.app:create_app --factory --port 8000
```

Open <http://localhost:8000>. Full walkthrough in
[docs/STARTUP.md](docs/STARTUP.md); the auth model, its hardening record and
its remaining gaps in [docs/AUTH_SECURITY.md](docs/AUTH_SECURITY.md).

No server, no browser:

```bash
.venv/bin/python -m agentic.pipeline "Summarise Q3 engineering spend"
```

## Google Cloud

Cartographer runs its Gemini half on **Vertex AI** and can back any department
with **Cloud Storage** instead of the local filesystem.

**Vertex AI** replaces the AI Studio free tier, whose 20 requests/day/model cap
allows roughly three runs. Authenticate once with user ADC and set three
variables — no service-account key, and nothing secret enters the repo:

```bash
gcloud auth application-default login
```

```bash
GOOGLE_GENAI_USE_VERTEXAI=TRUE
GOOGLE_CLOUD_PROJECT=your-project-id
GOOGLE_CLOUD_LOCATION=us-central1
```

**Cloud Storage** turns a department into a cloud-backed one. Upload the data
(pure Python, no `gcloud`/`gsutil` needed):

```bash
PYTHONPATH=src .venv/bin/python scripts/upload_departments.py \
    --bucket your-bucket --project your-project-id --department finance
```

then add a `storage:` block to that department in `config/environment.yaml`:

```yaml
  - name: finance
    path: data/departments/finance     # still required: offline identity + containment anchor
    allowed_roles: [analyst, admin]
    file_globs: ["**/*.md", "**/*.csv"]
    storage:
      provider: gcs
      bucket: your-bucket
      prefix: finance
      max_bytes: 5000000
```

Object keys are `<department>/<relative path>`, so the department name *is* the
bucket prefix and the prefix *is* the containment root. The permission gates do
not care which backend a file came from: a cloud file only survives if the role
is granted the department in both configs and the key stays under the prefix.
Nested keys keep their shape (`finance/vendor-contracts/cloud-renewal.md` reads
as `vendor-contracts/cloud-renewal.md`). `GET /api/fleet` reports which
departments are cloud-backed, so the badge in the UI is derived, not decorative.

Google Drive is supported the same way (`provider: drive`, a `folder_id`), with
the shared-drive grant taking the place of the bucket prefix.

The wire behaviour is pinned by tests that run the real `google-cloud-storage`
client against a stub JSON API ([`tests/test_cloud_gcs_wire.py`](tests/test_cloud_gcs_wire.py)),
so prefix containment and key→path mapping are covered without a network or a
bucket.

## Replay mode

A live run costs two API keys and can die on a quota wall halfway through a
demo. A recording is the same run frozen: the exact SSE frames the frontend
already consumes, written to disk once and streamed back on request, paced as
captured. Nothing is faked — a replay is a real run's real output.

```bash
# record (costs one real run)
PYTHONPATH=src .venv/bin/python scripts/record_demo.py \
    --role admin "Compare Q3 engineering spend to the finance budget"

# or record every run served by the server
AGENTIC_RECORD=1 .venv/bin/uvicorn agentic.server.app:create_app --factory
```

Recordings land in `recordings/` and appear in the app's left rail. They are
free, offline, and identical on screen to the run they came from.

**A replay is pinned to the role that recorded it.** A recording holds files
its principal was cleared to read, already past every gate; serving an admin
recording to an analyst would hand over exactly what the gates spent the run
refusing. `POST /api/replay/{name}` refuses a role mismatch with a 403, the
listing shows which role each recording needs rather than hiding it, and only
the saved graph pages under `recordings/pages/` are on a static mount — the
recordings themselves are reachable only through the endpoint that checks.

Record the same prompt as `analyst` and as `admin` and you have the entire
permission story as two clicks, with no API keys at all.

## The frontend

`src/agentic/server/static/index.html` — one self-contained page, no CDN, no
build step. The left rail is a control surface, not a log:

- **Fleet** from `GET /api/fleet`: which departments you may read, which are
  cloud-backed, which models are wired — all before spending a model call.
  Locked departments deliberately have no checkbox and are always in scope, so
  the gate can be seen denying them.
- **Run controls**: fan-out width, veto retry budget, veto model. Every one is
  bounded server-side — ints clamp to the operator's ceiling, the veto model
  must be in `models.veto_choices`, and the department list can only *narrow*.
- **Cancel**: `run_started` carries a `run_id`; only the user who started a run
  can cancel it, and another user's id returns 404 rather than 403 so the
  existence of the run isn't leaked.
- **Live graph**: departments, candidate files, reads, assessments and denials
  stream in as the agents work, then snap to the authoritative graph JSON at
  `run_state` so any drift in the live approximation self-corrects.

Everything model- or file-derived is inserted with `textContent`. The markdown
renderer escapes before it formats.

## Layout

```
src/agentic/
├── contracts/       # SHARED schemas — change only with both of us agreeing
├── state_manager/   # manager.py, planning, synthesizer (Malik) · output.py (Michael)
├── gatherers/       # spawn.py + permissions.py (Malik) · gather.py, cloud adapters (Michael)
├── veto/            # checker.py (Michael)
├── client/          # viz.py — the graph, shared by the app and standalone pages (Michael)
├── server/          # auth, FastAPI SSE backend, replay (Malik + Michael)
├── retry.py         # one retry policy for every model call
├── env.py           # .env loading, called from every entry point
└── pipeline.py      # end-to-end wiring + veto retry loop (Michael)

config/              # environment.yaml (departments, storage, models, limits)
                     # permissions.yaml (roles -> departments)
                     # users.yaml (accounts) — all three gitignored
data/departments/    # 18 fake enterprise files across engineering, finance, hr
docs/                # project page + startup and security docs
recordings/          # recorded runs for offline replay
scripts/             # password hashing, GCS upload, demo recording, viz demo
```

## Tests

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/ -q
```

202 tests. The pipeline is faked at the manager/checker/viz seams so the HTTP
surface, the permission gates, the override clamping, the replay role pin and
the GCS wire protocol are all covered without API keys. A handful of live tests
in `tests/test_checker.py` do call the real models and skip themselves when no
keys are loaded.

## Ownership

Built by Michael and Malik. [PLAN.md](PLAN.md) has the architecture and the
task split. `src/agentic/contracts/` is the seam between us: changing a field
there is a contract change, agreed before it lands, and
`server/schemas.py` carries the same rule for the HTTP surface.

## Known limits

- The veto checker runs on the Anthropic API and stops working when that
  account runs out of credit. Replay mode exists partly so a demo survives it.
- `/graphs` and `/replays` serve rendered graph pages without auth, so an
  unguessable filename is the only thing protecting a rendered answer. Fine for
  a demo, not for a deployment.
- The login rate limiter is per-process, so it only holds for a single
  instance. See [docs/AUTH_SECURITY.md](docs/AUTH_SECURITY.md) for the rest.
