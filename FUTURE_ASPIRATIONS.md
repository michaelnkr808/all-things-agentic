# Future Aspirations — Agentic Enterprise Pipeline

These are my goals for this project. This living document captures the
philosophy, what we are building for the hackathon, and the longer-horizon
vision. Keep it honest: mark what is built, what is in scope, and what is
aspirational.

## 1. Philosophy & hard invariants

These are non-negotiable design principles. Every feature must hold them.

- **Statelessness.** One loop per prompt. No model context carries across
  loops. Durable state lives only in the emitted artifact and the audit trail —
  never in the model's context. This makes the system Cloud Run-ready by
  construction (ephemeral containers, scale-to-zero, no session affinity).
- **Token economy.** Cheap models (Gemini Flash) do the bulk reading of the
  corpus (local / GCS / Drive). Mid-tier models condense. Expensive models
  (Claude) see only distilled artifacts, never the raw corpus. Cost and latency
  per tier are measured and surfaced.
- **Security as a must.** Every read and write crosses the same fail-closed
  permission gate (permissions config AND environment config AND path
  containment). No LLM in the write path — writes are deterministic, gated
  code. Scope never widens from model output.
- **No long-chain hallucination.** Loops are bounded (`veto.max_retries`).
  Claims are structurally tied to sources via inline citations, and an
  adversarial veto checker rejects unsupported output. Grounding is a gate, not
  a hope.

## 2. Hackathon scope (in build)

What we are delivering for the hackathon.

- **Write-back reporter.** Permission-scoped writes as a pure function
  (`reporter.publish`). `write_departments` in the permissions config,
  `write_roles` in the environment config, AND semantics, `write ⊆ read`
  enforced. Timestamped artifact names, never overwrite, no writes through
  symlinks. Backends: local (temp + `os.replace`), GCS (`if_generation_match`),
  Drive (`files.create`).
- **Hallucination protections.**
  - Structural grounding gate: every substantive claim carries an inline
    `[[dept/path]]` citation; the veto verifies citations resolve to listed
    sources, `departments_used ⊆ allowed`, prompt answered. Produces a
    groundedness score.
  - Sample-level verification hook (aspirational path, wired now, off by
    default): optional adversarial re-read of a sample of sources when budget
    allows.
- **`RunState` state dump.** Emitted immediately when the loop finishes:
  `CompiledOutput` + verdict history + write receipt + telemetry + audit
  entries. This is the frontend's contract and the demo timeline.
- **Telemetry & audit trail.** Per-tier token/cost/latency, gatherer fan-out
  count, denied count, veto retries. Every read/write decision logged.
- **SSE backend + separate frontend app.** FastAPI service: `POST /run`
  streams Server-Sent Events (planner → gatherers → veto → revise → write →
  final `RunState`); stateless one-way push, no server-side session state.
  Separate lightweight static SPA (prompt input, role selector, live event
  timeline, graph rendering, groundedness + telemetry panel, write receipt).
  `render_html` stays as the offline artifact.
- **Deterministic demo mode.** `DEMO_MODE=1` canned planner/synthesizer/veto
  (rigged first-veto-then-approve) so the live, unedited demo cannot die to a
  rate limit or network flake. Real gather + gate + write still execute.
- **GCP deployment.** API + frontend on Cloud Run, service-account key in
  Secret Manager, real GCS bucket and Google Drive departments (scripted via
  `drive_setup.py`), reproducible `setup.sh`.
- **Contracts (shared, sync required):** `WriteRequest` / `WriteReceipt`,
  `RunState`, emitter inline-citation format, `write_roles` /
  `write_departments` fields.

## 3. Aspirational (post-hackathon / stretch)

Ideas that extend the system beyond the hackathon. Roughly ordered by value vs.
effort; none are committed.

- **Sample-level adversarial verification at scale.** Turn the config-gated
  hook into a tiered reviewer: verify a sampling of claims against source
  content, with confidence-based adaptive sampling.
- **Multi-turn without breaking statelessness.** Keep server stateless; let the
  client hold session context and re-send a compact, self-contained prompt
  (with prior `RunState` summaries) each turn.
- **MCP integration.** Expose gathered files as resources to the strong model
  via Model Context Protocol, keeping the token economy.
- **Human-in-the-loop approval.** Locked departments require a human approval
  step before reads or writes complete; gives a compliance story.
- **Prompt-injection defense.** Anomaly scoring on gathered content to detect
  injected instructions before synthesis.
- **Cost-aware model routing.** Dynamically pick model tier per query
  complexity and budget, based on telemetry.
- **Cross-run analytics on the audit store.** Trends, "why denied" reporting,
  compliance summaries, per-role access review.
- **Real user identities.** OAuth principals instead of a single service-account
  role; map tokens to `principal.role`.
- **Live streaming graph.** Force-directed graph that updates in real time as
  gatherers return files during fan-out.
- **Self-healing gatherers.** Retry with backoff and degraded-mode badges so a
  failed department never sinks the run.

## 4. Rubric alignment

How the work maps to the hackathon rubric.

- **Innovation & Operational Utility (40%).** Write-back = autonomous,
  high-value action: one prompt, a permission-scoped deliverable lands in the
  right department. Role comparison beat (analyst vs admin on the same prompt)
  makes the security architecture visible.
- **Architectural Discipline & Tech Stack (30%).** Stateless by construction,
  Secret Manager credential handling, fail-closed gate on every read/write,
  audit trail, deterministic failure mode.
- **Demo & Production Readiness (30%).** Live unedited SSE-streamed demo,
  `RunState` trace, visible run on Cloud Run against real GCS + Drive,
  reproducible `setup.sh`, clean architecture diagram.
