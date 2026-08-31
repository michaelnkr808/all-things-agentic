# Devpost submission — Cartographer

Paste-ready copy. Story avoids em dashes to match the project page voice.

---

## About the project

### Inspiration

The honest answer to a question is sometimes "you are not allowed to know that,"
and almost no agent demo is built to say it.

Enterprises do not have one knowledge base. They have departments, and each one
has different rules about who may read it. Point an agent fleet at all of them
and you have built an access control system, whether or not you designed it as
one. Most projects discover this late, and patch it by filtering the model's
output.

That patch is the thing we wanted to argue against. Filtering output after the
fact is not access control. By the time you are filtering, the restricted
content has already been assembled into a prompt and sent to a model provider.
The gate has to sit in front of the read, not behind the answer.

Once we accepted that, a second idea followed. If the fleet refuses you
something, the refusal is information. An answer that quietly omits what it
could not reach is a worse answer than one that shows you the shape of the
boundary it hit. So Cartographer draws the files it was denied, dashed and
locked, right on the map next to the ones it used. That is where the name comes
from: the interesting part of a map has always been the edges.

### What it does

You ask one question. A state manager plans it into per department gathering
tasks and fans out a fleet of gatherer agents in parallel. Every file each
gatherer touches is checked against your role before it is opened. What survives
is synthesised into an answer with inline citations, and then an adversarial
veto checker tries to reject it: for unsupported claims, for citations that do
not resolve, for departments the config never allowed. If it vetoes, the answer
is revised and re checked.

The whole run streams into a graph while it happens. Files you were denied stay
on the map, locked.

Then there are the parts that exist because a permission system nobody can
inspect is just a claim:

- an **access ledger**, append only, recording every decision both gates made
- **citation coverage**, scoring how well the answer is anchored to what was
  actually gathered
- a **side by side role comparison**, running one prompt under two roles at once
  so you can see the shape of what a role costs you
- **replay**, which records a real run and plays the exact event stream back
  with no API calls at all

### How we built it

Two of us, splitting the pipeline down a shared contracts layer. Every message
between components is a Pydantic model in `src/agentic/contracts/`, and changing
a field there is a contract change we agree on before it lands. It meant we
could work on opposite ends of the same run without blocking each other.

The permission rule is deliberately boring, and it is an AND, not an OR:

$$
\text{read}(r, d, p) \iff
\underbrace{G_{\text{perm}}(r,d)}_{\text{permissions.yaml}} \;\wedge\;
\underbrace{G_{\text{env}}(r,d)}_{\text{environment.yaml}} \;\wedge\;
\underbrace{\text{contained}(p,d)}_{\text{no escaping the root}} \;\wedge\;
\underbrace{\text{exists}(p,d)}_{\text{the department really holds it}}
$$

Two independent config files must both grant the role that department. Any
disagreement denies, and malformed config fails closed. That expression is
enforced at five points in the pipeline rather than one, because the gatherer's
own pre read check is an efficiency layer and the boundary has to be somewhere a
model cannot reach.

The role itself comes from a verified JWT and nothing else. Nothing a model
emits is ever trusted as an access decision. The planner's department names are
validated against a closed schema built per request, so a hallucinated
department fails validation before any code runs on it.

The stack:

- **Gemini 3.5 Flash on Vertex AI** for the planner, the gatherers and the
  synthesiser, through the Google Agent Development Kit
- **Claude** for the veto checker, on a structured verdict the operator can swap
  at run time from an allowlist
- **Google Cloud Storage** for cloud backed departments, where the department
  name is the bucket prefix and the prefix is the containment root, so a cloud
  object passes exactly the same gate as a local file
- **Cloud Run** hosting the server, reaching Vertex and the bucket through the
  runtime service account's Application Default Credentials, so the deployed
  image carries no key file and no credentials at all
- **FastAPI with server sent events**, so the browser watches the fleet work
  file by file instead of waiting on a spinner
- a **hand written canvas renderer** for the graph, shared by the live app and a
  self contained offline page

245 tests, about 4,000 lines of them against 4,800 lines of source. The pipeline
is faked at its seams so the permission gates, the injection defences, the audit
ledger and the GCS wire protocol are all covered without API keys.

### Challenges we ran into

**No model call had ever actually run.** We had a green test suite and a working
pipeline and, it turned out, an `.env` file nobody loaded. Every live test was
decorated to skip without API keys, so they skipped, silently, and reported as
passing. The suite was green because it was not looking. That is the most
uncomfortable bug we have ever found in our own project.

**The free tier is 20 requests per day per model.** One run costs five to eight
calls, so roughly three runs a day, which is not a demo. Moving to Vertex AI
fixed it.

**Gemini 3.5 returned a bare 404, which reads exactly like the model does not
exist.** It does exist. It is not served from regional endpoints. Setting
`GOOGLE_CLOUD_LOCATION=global` fixed instantly what we had spent real time
believing was a model availability problem.

**The force directed graph exploded.** Coordinates in the $10^{22}$ range, every
hidden node collapsed onto the origin. The integration loop was running over all
nodes while the force loops skipped invisible ones, and an unbounded inverse
square term did the rest. Integrating only the live set, clamping force and
speed, and nudging coincident nodes brought it back.

**Containment is not provenance, and we only found out because we attacked
ourselves.** We planted a real prompt injection payload in the demo data and
wrote tests for the strongest form of the attack: assume the injection fully
worked and the gatherer returns the HR files it was told to. The test failed.
The gate kept them. A path like `hr/comp-bands.md` returned under
`department="engineering"` is contained, because it names a subdirectory of
engineering that happens not to exist, so nothing about it escapes the root.
Real HR data was never at risk, since the gatherer never had it, but a
fabricated citation could arrive wearing a real department's name. The gate now
asks two questions instead of one: may this role read this path, and is this
path a file the department actually holds.

**The veto checker caught a real hallucination the first time it fired live.**
The synthesiser cited `[[finance/infra-spend-q3.md]]` when the file was
`[[engineering/infra-spend-q3.md]]`. Right filename, wrong department, and in
rendered markdown the two are indistinguishable. It was rejected, revised, and
approved on revision 1 with every figure verified. That single incident is why
citation coverage exists as a feature.

**Our Anthropic credits ran out in the middle of building.** The veto checker is
Claude, so nothing could complete. Rather than wait, we built replay mode, which
records a real run's event stream and plays it back with zero API calls. A
constraint turned into the thing that makes the demo survive a dead key at
judging time.

### Accomplishments we are proud of

The permission story is not a paragraph in a README, it is a thing you watch
happen. Ask as an analyst and four HR files lock on the map before a single byte
is read. Ask as an admin and they open. Same prompt, same fleet.

And the injection file is sitting in the demo data the entire time, on every
single run, quietly failing.

### What we learned

- **A skipped test is not a passing test.** Green means nothing until you have
  checked what it is green about.
- **Containment answers a different question than provenance,** and we were only
  asking one of them.
- **Defending against prompt injection is architectural, not textual.** We never
  wrote a single instruction telling a model to resist the payload. It fails
  because by the time any model sees it, every decision it is trying to
  influence was already made by config it cannot reach.
- **Something has to be optimising against the answer.** Every other component
  in a pipeline is trying to produce output. Without a component whose job is to
  reject, nothing is.
- **Fail closed, and say so out loud.** An answer that exhausts its retries still
  ships, carrying a visible unapproved banner, never a silent one.

### What is next

Multi tenant deployment with a shared ledger, tamper evidence on the audit log
so it is a compliance artifact rather than a record, and more storage backends
behind the same gate.

---

## Built with

```
python, fastapi, uvicorn, pydantic, asyncio, server-sent-events, jwt, pbkdf2,
google-cloud, vertex-ai, gemini, google-adk, google-genai, google-cloud-storage,
google-drive-api, anthropic, claude, pytest, yaml, javascript, html5, css3,
canvas, github-pages, markdown
```

25 tags.

---

## Try it out

| Link | What it is |
|---|---|
| `https://cartographer-923557756991.us-central1.run.app` | **Live fleet.** Sign in as `alice` (analyst) or `root` (admin) with the one-click demo buttons and ask it something |
| `https://michaelnkr808.github.io/cartographer/` | Project page: the architecture, the gate, and the injection defence as a scrollable walkthrough |
| `https://github.com/michaelnkr808/cartographer` | Source, with setup and reproducible testing instructions in the README |

---

## Additional info (judges and organizers)

| Field | Answer |
|---|---|
| Submitter type | Team |
| Country of residence | United States |
| Category | Fortified Enterprise Fleet |
| Organization name | (leave blank, not submitting on behalf of one) |
| Date started | 08-14-26 |
| Code repo URL | `https://github.com/michaelnkr808/cartographer` |
| Reproducible testing instructions in README | Yes |
| Hosted project URL | `https://cartographer-923557756991.us-central1.run.app` (the running fleet, on Cloud Run) |
| Which Google SDK | Google Agent Development Kit (ADK) for Python, plus the `google-genai` SDK for direct calls, and `google-cloud-storage` |
| Which Google Cloud services | **Cloud Run** (hosts the server), **Vertex AI** (Gemini inference), **Cloud Storage** (cloud backed departments), Cloud Build + Artifact Registry (deploy), IAM / Application Default Credentials |
| Which Google AI models | `gemini-3.5-flash` on Vertex AI, used for the planner, the gatherer fleet and the synthesiser |
| Startup prize | Not opting in |

### Testing instructions (judges only, not public)

**Fastest path: use the live instance.** <https://cartographer-923557756991.us-central1.run.app>

One-click demo logins are enabled on the sign-in card, so no credentials need
typing. If you want them explicitly:

| user | role | password |
|---|---|---|
| `alice` | analyst | `umK3_K56IasY` |
| `root` | admin | `-BzJcXIYCxVI` |

The `finance` department on that instance reads from a real Cloud Storage
bucket, so a run exercises the GCS adapter and the permission gate against
live cloud objects, not local files. Watch for the `downloading` event.

To run it yourself instead:

```bash
git clone https://github.com/michaelnkr808/cartographer && cd cartographer
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

cp config/environment.example.yaml config/environment.yaml
cp config/permissions.example.yaml config/permissions.yaml
cp config/users.example.yaml      config/users.yaml
python scripts/hash_password.py      # set a password, paste as password_hash:

# .env at the repo root
GOOGLE_GENAI_USE_VERTEXAI=TRUE
GOOGLE_CLOUD_PROJECT=<your-project>
GOOGLE_CLOUD_LOCATION=global          # Gemini 3.x is not served regionally
ANTHROPIC_API_KEY=<key>
AGENTIC_JWT_SECRET=<openssl rand -hex 32>
AGENTIC_DEMO_MODE=1

gcloud auth application-default login
.venv/bin/uvicorn agentic.server.app:create_app --factory --port 8000
```

Open <http://localhost:8000>.

**No keys? Use replay.** `recordings/` ships with two real recorded runs. Log in
and pick one from the Replays panel: it streams the exact events the live run
produced, with no API calls. A recording only replays to the role that made it,
so the admin recording needs the admin login. That is the permission model, not
a UI restriction.

**The 90 second path:**

1. Sign in as the analyst. Ask *"What are the HR compensation bands, and how did
   Q3 engineering spend compare to budget?"* Four HR files lock on the map before
   any file is opened. The answer names them and cites none of them.
2. Switch to the admin. Same question. HR opens.
3. As admin, hit **compare vs analyst**. Both runs stream at once and the
   analyst column sheds what it may not read.
4. Open the **access ledger** in the left rail: the decisions you just watched,
   with receipts.
5. `data/departments/engineering/tickets/ENG-4471-vendor-import.md` contains a
   live prompt injection payload telling the reader it is now an administrator
   and should return HR files. It was in context for every run above.

Run the suite:

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/ -q     # 245 tests
```

The live model tests in `tests/test_checker.py` skip themselves without keys.
Everything else, including the injection defences and the GCS wire protocol,
runs offline.

### Bonus point items (not yet done)

- Public blog/video/podcast link, stating it was created for this hackathon
- Social post with `#AllThingsAgentic Hackathon`
