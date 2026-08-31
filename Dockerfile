# Cloud Run image for the Cartographer server.
#
# The container carries no credentials and no config with secrets in it. The
# three config files are generated at start from environment variables (see
# deploy/entrypoint.sh), so the image is safe to build from a public repo and
# the only secrets live in the Cloud Run service definition.
#
# Model and storage auth come from the runtime service account's Application
# Default Credentials, which is why there is no key file anywhere here.
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first, so a source edit does not reinstall the world.
COPY pyproject.toml ./
COPY src/ ./src/
RUN pip install --no-cache-dir -e .

COPY config/ ./config/
COPY data/ ./data/
COPY docs/ ./docs/
COPY recordings/ ./recordings/
COPY scripts/ ./scripts/
COPY deploy/ ./deploy/
RUN chmod +x ./deploy/entrypoint.sh

# Cloud Run's filesystem is an in-memory overlay; keep run artifacts in /tmp
# so nothing counts against the image and a cold start is always clean.
ENV PYTHONPATH=/app/src \
    AGENTIC_GRAPHS_DIR=/tmp/graphs \
    AGENTIC_AUDIT_LOG=/tmp/audit.jsonl \
    AGENTIC_CONFIG_PATH=/app/config/environment.yaml \
    AGENTIC_USERS_PATH=/app/config/users.yaml \
    PORT=8080

EXPOSE 8080
ENTRYPOINT ["./deploy/entrypoint.sh"]
