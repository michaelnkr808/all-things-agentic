# Design — Standalone Auth Service

**Status:** Approved 2026-06-12. Shipped 2026-08-19.

Splits authentication out of the monolith. Tokens are stateless JWTs with a
12-hour TTL; the service holds no session state, so it scales horizontally.

## Decisions
- PBKDF2-HMAC-SHA256 for password storage (no new crypto dependency).
- Roles resolve at login and are carried in the token claim, never accepted
  from a request body.
- Fail closed: an unknown role is rejected rather than defaulted.
