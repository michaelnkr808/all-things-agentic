#!/usr/bin/env bash
# Deploy the Cartographer server to Cloud Run.
#
#   ~/google-cloud-sdk/bin/gcloud auth login     # once, interactive
#   ./deploy/deploy.sh
#
# Secrets are read from .env and passed straight to the service definition;
# none of them are echoed, written into the image, or committed. Model and
# bucket access come from the runtime service account's ADC, so there is no
# key file to manage.
set -euo pipefail

GCLOUD="${GCLOUD:-$HOME/google-cloud-sdk/bin/gcloud}"
PROJECT="${GOOGLE_CLOUD_PROJECT:-cartographer-507021}"
REGION="${DEPLOY_REGION:-us-central1}"     # where the service runs
VERTEX_LOCATION="global"                   # where Gemini 3.x is served
SERVICE="${SERVICE_NAME:-cartographer}"

cd "$(dirname "$0")/.."

[ -f .env ] || { echo "no .env at repo root" >&2; exit 1; }
ANTHROPIC_API_KEY="$(grep -E '^ANTHROPIC_API_KEY=' .env | head -1 | cut -d= -f2-)"
[ -n "$ANTHROPIC_API_KEY" ] || { echo "ANTHROPIC_API_KEY missing from .env" >&2; exit 1; }

# Credentials are minted once and then reused. A redeploy that silently rotated
# them would invalidate whatever has already been published (a Devpost entry, a
# README, a judge's notes), so existing values are read back off the running
# service and only generated when there is nothing to read.
read_deployed_env() {
  "$GCLOUD" run services describe "$SERVICE" --project "$PROJECT" --region "$REGION" \
    --format="value(spec.template.spec.containers[0].env.filter(\"name:$1\").extract(value))" \
    2>/dev/null | head -1
}

JWT_SECRET="${AGENTIC_JWT_SECRET:-$(read_deployed_env AGENTIC_JWT_SECRET)}"
ANALYST_PW="${DEMO_ANALYST_PASSWORD:-$(read_deployed_env DEMO_ANALYST_PASSWORD)}"
ADMIN_PW="${DEMO_ADMIN_PASSWORD:-$(read_deployed_env DEMO_ADMIN_PASSWORD)}"

# The JWT secret is per-deployment infrastructure, never published, so unlike
# the demo passwords it is safe to regenerate when absent.
[ -n "$JWT_SECRET" ] || JWT_SECRET="$(python3 -c 'import secrets;print(secrets.token_hex(32))')"
[ -n "$ANALYST_PW" ] || { ANALYST_PW="$(python3 -c 'import secrets;print(secrets.token_urlsafe(9))')"; echo "==> minted a new analyst password"; }
[ -n "$ADMIN_PW" ]   || { ADMIN_PW="$(python3 -c 'import secrets;print(secrets.token_urlsafe(9))')"; echo "==> minted a new admin password"; }

echo "==> enabling APIs on $PROJECT"
"$GCLOUD" services enable run.googleapis.com cloudbuild.googleapis.com \
  artifactregistry.googleapis.com --project "$PROJECT" --quiet

echo "==> building and deploying $SERVICE to $REGION"
"$GCLOUD" run deploy "$SERVICE" \
  --source . \
  --project "$PROJECT" \
  --region "$REGION" \
  --allow-unauthenticated \
  --memory 1Gi \
  --cpu 1 \
  --timeout 900 \
  --concurrency 8 \
  --max-instances 3 \
  --set-env-vars "GOOGLE_GENAI_USE_VERTEXAI=TRUE,GOOGLE_CLOUD_PROJECT=$PROJECT,GOOGLE_CLOUD_LOCATION=$VERTEX_LOCATION,AGENTIC_DEMO_MODE=1,ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY,AGENTIC_JWT_SECRET=$JWT_SECRET,DEMO_ANALYST_PASSWORD=$ANALYST_PW,DEMO_ADMIN_PASSWORD=$ADMIN_PW" \
  --quiet

URL="$("$GCLOUD" run services describe "$SERVICE" --project "$PROJECT" \
        --region "$REGION" --format='value(status.url)')"

echo
echo "  live:    $URL"
echo "  analyst: alice / $ANALYST_PW"
echo "  admin:   root  / $ADMIN_PW"
echo
echo "  tear down after judging:"
echo "    $GCLOUD run services delete $SERVICE --project $PROJECT --region $REGION"
