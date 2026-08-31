#!/usr/bin/env sh
# Generate the three runtime configs, then start the server.
#
# config/*.yaml are gitignored and never baked into the image. environment and
# permissions are copied from the committed examples; users.yaml is built here
# from DEMO_ANALYST_PASSWORD / DEMO_ADMIN_PASSWORD so no credential, hashed or
# otherwise, is ever committed or layered into the image.
set -e

cd /app

# The cloud config points `finance` at the GCS bucket; fall back to the
# all-local example if it is not present in the image.
if [ ! -f config/environment.yaml ]; then
  if [ -f deploy/environment.cloud.yaml ]; then
    cp deploy/environment.cloud.yaml config/environment.yaml
  else
    cp config/environment.example.yaml config/environment.yaml
  fi
fi
[ -f config/permissions.yaml ] || cp config/permissions.example.yaml config/permissions.yaml

python - <<'PY'
import os, sys
sys.path.insert(0, "/app/src")
from agentic.server import auth

analyst = os.environ.get("DEMO_ANALYST_PASSWORD")
admin = os.environ.get("DEMO_ADMIN_PASSWORD")
if not analyst or not admin:
    raise SystemExit("DEMO_ANALYST_PASSWORD and DEMO_ADMIN_PASSWORD must be set")

# demo_password is only honoured when AGENTIC_DEMO_MODE=1; without that flag
# the server serves no credentials regardless of what is in this file.
lines = ["users:"]
for name, role, pw in (("alice", "analyst", analyst), ("root", "admin", admin)):
    lines += [
        f"  {name}:",
        f"    role: {role}",
        f"    password_hash: {auth.hash_password(pw)}",
        f"    demo_password: {pw}",
    ]
open("/app/config/users.yaml", "w").write("\n".join(lines) + "\n")
print(f"users.yaml written for: alice, root", flush=True)
PY

exec uvicorn agentic.server.app:create_app --factory \
     --host 0.0.0.0 --port "${PORT:-8080}" --timeout-keep-alive 900
