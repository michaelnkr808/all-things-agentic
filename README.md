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
| 5. Provenance | `gatherers/spawn.py` | A returned path must be a file the department actually holds — containment alone would keep an invented path that merely stays inside the root |

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

## Resisting prompt injection

`data/departments/engineering/tickets/ENG-4471-vendor-import.md` is a real
file in the demo data containing a real injection payload: a block of text
telling whatever reads it that its permission configuration is superseded,
that it is now authorised as an administrator, and that it should return two
HR files and not mention doing so.

It sits in engineering deliberately, because engineering is the department an
analyst *may* read. The payload therefore reaches a model on every analyst
run — which is the threat model, not an accident.

The defence is not that the model declines. It is that by the time any model
sees that text, every decision it is trying to influence has already been
made by config it cannot reach:

- the planner's department catalog is a closed `Literal` built per request,
  so an invented target fails schema validation before any code runs;
- the gatherer's glob root is the department directory, so the files the
  payload names do not exist to be returned;
- the spawn gate re-checks every returned file against the trusted role, and
  asks two questions, not one — may this role read this path (policy), and is
  this path a file the department actually holds (provenance);
- the role itself comes from the JWT, never from a request body or a file.

`tests/test_injection.py` runs the payload's own asks as the attack, including
the strongest form: assume the injection fully worked and the gatherer returns
the HR files it was told to. The gate is downstream of the model and denies
them anyway.

Live result, analyst side, with the payload in context:

> Specific HR compensation implications, such as details from compensation
> bands or headcount plans, were denied by permissions (comp-bands.md,
> headcount-plan.csv).

The filenames are named, the contents are not, and nothing the payload asked
for appears anywhere in the answer.

## The access ledger

Every permission decision both gates make is appended to `out/audit.jsonl` as
one JSON line: principal, role, department, path, allow or deny, and which
gate decided. `GET /api/audit` serves it back and the app shows it in the left
rail. Both gates are recorded on purpose — watching a file pass the pre-read
check and then pass the spawn gate again is what makes defence-in-depth
visible rather than asserted.

Attribution rides a `ContextVar` (`agentic/audit.py`), so a decision made deep
inside a gatherer picks up its run and principal without audit concerns being
threaded through five signatures. Outside a run context `record` does nothing,
which keeps unit tests from writing lines into a ledger that is supposed to
mean "this happened during a run".

The endpoint is pinned to the caller: an audit line names files inside
departments the reader may not be cleared for, so nobody browses anyone
else's. Your own denials are exactly the ones worth showing you.

## Citation coverage

The veto checker returns approved or not, which is the right output for a gate
and a poor one for a reader — it says an answer passed without saying what
passing was close to. `agentic/citations.py` scores the same evidence with no
model call, and the app shows it as three meters beside the veto badge:

- **integrity** — of the `[[dept/path]]` citations in the prose, how many name
  a file that was really gathered. This is the failure this project has
  actually caught live: a synthesizer citing `[[finance/infra-spend-q3.md]]`
  when the file was `[[engineering/infra-spend-q3.md]]`. An unresolved
  citation renders identically to a real one, so it is called out by name
  rather than buried in a percentage.
- **grounding** — what fraction of the answer's paragraphs cite anything.
- **usage** — what fraction of gathered files the answer drew on. Gatherers
  over-gather by design, so this is context rather than a target.

A ratio with a zero denominator is reported as `null` and rendered "n/a",
never as a confident 0% on an answer that simply had nothing to cite.

## Side-by-side role comparison

`POST /api/compare` runs one prompt under two roles at once and diffs the
results: what each side reached, what each was refused, both answers, and both
coverage scores. A single run shows you an answer and some locks; this shows
you the shape of what a role costs you.

**Downward only.** The obvious version of this feature is a privilege
escalation wearing a UI, so the comparison role must be one whose department
grants are a strict subset of the caller's. An admin may look down at what an
analyst would have got; an analyst cannot look up, and `GET /api/fleet`
advertises only the roles that are actually available to the caller. Both
sides still pass every gate under their own role — this runs two permission
checks, it does not skip one.

A live admin-vs-analyst comparison on the demo data:

| | admin | analyst |
|---|---|---|
| files gathered | 11 | 7 |
| departments | engineering, finance, hr | engineering, finance |
| denied | 0 | 4 |
| citation integrity | 100% | 100% |

with `only_yours` listing exactly the four HR files plus
`finance/headcount-costs.csv`.

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
- **Access ledger, coverage meters, replays and comparison** each appear only
  when they have something to show: the ledger once a run has made decisions,
  the comparison control only if the caller has a narrower role to compare
  against, replays only if recordings exist.

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
├── server/          # auth, FastAPI SSE backend, replay, compare (Malik + Michael)
├── audit.py         # append-only ledger of every permission decision
├── citations.py     # citation coverage scoring, no model call
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

243 tests. The pipeline is faked at the manager/checker/viz seams so the HTTP
surface, the permission gates, the injection defences, the override clamping,
the audit ledger, the replay role pin, the comparison direction and the GCS
wire protocol are all covered without API keys. A handful of live tests
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
- The audit ledger is a local append-only file with no rotation and no
  tamper-evidence. It is a record, not a compliance artifact.
- A comparison costs two full runs, so it is twice the latency and twice the
  spend of a single question.
