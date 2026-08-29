# Auth security model (Cartographer server)

Owner: Malik (provisional — ping before building hard dependencies on this).

This documents how authentication works, the hardening pass it went through
(2026-08-26), and the rules operators must follow to keep it true.

## How auth works

```
POST /api/login {username, password}
        │
        ▼
config/users.yaml ──► PBKDF2-SHA256 verify (390k iter, salted,
        │             constant-time compare — auth.verify_password)
        ▼
role must exist in config/permissions.yaml  ── else fail closed (403)
        │
        ▼
HS256 JWT {sub, role, iat, exp}   (stateless; TTL default 12h)
        │
        ▼
Authorization: Bearer <jwt> on /api/fleet · /api/run · /api/run/{id}/cancel
```

Invariants that must never break:

- **The authenticated JWT role is the only principal.** It threads into every
  permission gate (`spawn` gate re-verifies too). Nothing is accepted from
  request bodies or model output.
- **Fail closed everywhere.** Missing `AGENTIC_JWT_SECRET`, missing/broken
  `users.yaml`, malformed stored hashes, unknown roles — all refuse to
  authenticate rather than fall back.
- **Unknown user and wrong password are indistinguishable** to the client
  (both `401 invalid credentials`).
- Config paths are operator-controlled env vars, never request fields.

## Hardening record (2026-08-26)

What was found and fixed:

| Finding | Was | Now |
|---|---|---|
| Working demo passwords shipped in frontend HTML/JS (`data-p` attributes + a second copy in the switch-user handler) — readable via view-source | `index.html` held `analyst-demo-pass` / `admin-demo-pass` verbatim | Zero credentials in static assets. Demo buttons render from `GET /api/demo-mode` at runtime |
| Live accounts used those public passwords; they were also committed in `users.example.yaml` | `config/users.yaml` mirrored the public example file | Both rotated to random secrets. Example file ships placeholder hashes only |
| `/api/login` had no rate limiting — trivially brute-forceable | Unlimited attempts | Per-IP sliding-window limiter: 5 failures/min → `429`. Only failures count; a successful login clears the bucket |
| Demo autofill always on | One-click login for anyone | Gated behind `AGENTIC_DEMO_MODE=1`; off by default |

What was already solid (unchanged): salted PBKDF2-SHA256 at 390k iterations,
constant-time comparison, secret required from the environment (no default),
tokens kept in browser memory only (no localStorage, no cookies), owner-only
run cancellation authorized against the JWT.

## Demo mode contract

Demo identity autofill exists so a presenter can log in as two roles with one
click. It is strictly opt-in, end to end:

1. `config/users.yaml` gains an optional per-user `demo_password:` field
   (plaintext). The file is gitignored and operator-owned.
2. `GET /api/demo-mode` returns `{enabled: false}` unless **both**:
   - `AGENTIC_DEMO_MODE=1` is set, and
   - users.yaml entries actually carry `demo_password`.
3. When enabled it serves `{username, role, password}` per identity; the
   frontend renders the buttons from that payload and fills the login form.

Rules:

- `demo_password` MUST equal the real password (the frontend submits it as
  such) and MUST be omitted entirely outside local demos.
- Never commit a real `users.yaml`. The example file stays placeholder-only;
  do not reintroduce "demo password" comments there.
- Demo mode on a network-exposed host means serving working passwords to
  anyone who opens dev tools. Local demos only.

## Login rate limiting

`auth.LoginRateLimiter` (`src/agentic/server/auth.py`):

- Sliding 60s window, max 5 failed attempts per client IP → `429 Too Many
  Requests` until failures age out of the window.
- Successful logins clear the IP's history so legitimate users are never
  locked out behind attacker noise.
- **Per-process only** (stdlib dict). Correct for the current single-instance
  deployment shape. Before running multiple replicas, back this with a shared
  store (Redis etc.) or limits reset per instance.

## Known remaining gaps

Stated honestly so nobody assumes otherwise:

- **JWTs are not revocable.** Stateless by design (Cloud Run-ready); a leaked
  token is valid until expiry. Keep `AGENTIC_JWT_TTL_HOURS` short if tokens
  leave your laptop.
- **`AGENTIC_DEV_CORS=1` allows all origins.** Dev convenience for a
  separately-served frontend. Must stay unset in anything exposed.
- **Rate limiter keys on `request.client.host`.** Behind a proxy that
  terminates TLS you'll need forwarded-header handling or every client shares
  the proxy's bucket.

## Rotating credentials

```bash
python scripts/hash_password.py            # prompts securely, prints a hash line
# paste into config/users.yaml under the user's password_hash:
```

Then restart the server (users.yaml is re-read per login, but restart makes
rotation unambiguous) and update any `demo_password` fields to match.
