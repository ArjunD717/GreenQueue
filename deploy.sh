#!/usr/bin/env bash
#
# Build, deploy, seed, and publish GreenQueue in one pass.
# Usage:
#   ./deploy.sh                          # synthetic forecast, reliable demo
#   MOCK_MODE=false ./deploy.sh          # live UK Carbon Intensity API
#   MOCK_MODE=false SKIP_SEED=true ./deploy.sh
#
set -euo pipefail

STACK_NAME="${STACK_NAME:-greenqueue}"
REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-eu-west-2}}"
MOCK_MODE="${MOCK_MODE:-true}"
ALLOWED_ORIGIN="${ALLOWED_ORIGIN:-*}"
SKIP_SEED="${SKIP_SEED:-false}"
DIST_DIR=".build-dashboard"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

export AWS_PAGER=""
export AWS_DEFAULT_REGION="$REGION"

if [ "$MOCK_MODE" != "true" ] && [ "$MOCK_MODE" != "false" ]; then
  echo "MOCK_MODE must be true or false." >&2
  exit 1
fi

if command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="${PYTHON_BIN:-python3}"
elif command -v python >/dev/null 2>&1; then
  PYTHON_BIN="${PYTHON_BIN:-python}"
else
  echo "Python is required to build the dashboard and seed DynamoDB." >&2
  exit 1
fi

for command in sam aws; do
  if ! command -v "$command" >/dev/null 2>&1; then
    echo "Missing $command. Install the AWS CLI v2 and AWS SAM CLI, then retry." >&2
    exit 1
  fi
done

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text 2>/dev/null || true)"
if [ -z "$ACCOUNT_ID" ] || [ "$ACCOUNT_ID" = "None" ]; then
  echo "AWS credentials are not configured. Run: aws configure" >&2
  exit 1
fi

echo "Deploying GreenQueue to account $ACCOUNT_ID in $REGION"
echo "Stack: $STACK_NAME | Mock mode: $MOCK_MODE"

echo
echo "==> Building SAM application"
sam build --template-file template.yaml

echo
echo "==> Deploying stack"
sam deploy \
  --template-file .aws-sam/build/template.yaml \
  --stack-name "$STACK_NAME" \
  --region "$REGION" \
  --resolve-s3 \
  --capabilities CAPABILITY_IAM \
  --no-confirm-changeset \
  --no-fail-on-empty-changeset \
  --parameter-overrides "MockMode=$MOCK_MODE" "AllowedOrigin=$ALLOWED_ORIGIN" \
  --tags project=greenqueue

stack_output() {
  aws cloudformation describe-stacks \
    --stack-name "$STACK_NAME" \
    --region "$REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue | [0]" \
    --output text
}

API_URL="$(stack_output ApiUrl)"
WEBSITE_URL="$(stack_output WebsiteUrl)"
BUCKET_NAME="$(stack_output WebsiteBucketName)"
TABLE_NAME="$(stack_output JobsTableName)"

for value in "$API_URL" "$WEBSITE_URL" "$BUCKET_NAME" "$TABLE_NAME"; do
  if [ -z "$value" ] || [ "$value" = "None" ]; then
    echo "The stack deployed but its outputs are incomplete. Check CloudFormation." >&2
    exit 1
  fi
done

echo
echo "==> Injecting API URL and publishing the dashboard"
rm -rf "$DIST_DIR"
mkdir -p "$DIST_DIR"
API_URL="$API_URL" "$PYTHON_BIN" - "$DIST_DIR/index.html" <<'PY'
import json
import os
import pathlib
import sys

source_path = pathlib.Path("dashboard/index.html")
destination_path = pathlib.Path(sys.argv[1])
source = source_path.read_text(encoding="utf-8")
api_url = os.environ["API_URL"].rstrip("/")

if api_url.startswith("http://"):
    raise SystemExit("The API URL must use HTTPS.")

output = source.replace('"__API_BASE_URL__"', json.dumps(api_url))
if "__API_BASE_URL__" in output:
    raise SystemExit("The API placeholder was not found in dashboard/index.html.")

destination_path.write_text(output, encoding="utf-8")
print(f"Dashboard configured for {api_url}")
PY

aws s3 sync "$DIST_DIR/" "s3://$BUCKET_NAME/" \
  --region "$REGION" \
  --delete \
  --cache-control "no-cache"

echo
echo "==> Seeding DynamoDB"
if [ "$SKIP_SEED" = "true" ]; then
  echo "Skipped at your request (SKIP_SEED=true)."
elif ! "$PYTHON_BIN" seed.py --table "$TABLE_NAME" --region "$REGION"; then
  echo "WARNING: Seeding failed. The stack is live, but the queue will start empty." >&2
  echo "         Run: $PYTHON_BIN seed.py --table $TABLE_NAME --region $REGION" >&2
fi

echo
echo "GreenQueue is deployed."
echo "Dashboard: $WEBSITE_URL"
echo "API:       $API_URL"
echo "Table:     $TABLE_NAME"
if [ "$MOCK_MODE" = "true" ]; then
  echo "Forecast:  synthetic (set MOCK_MODE=false for live UK grid data)"
else
  echo "Forecast:  live UK Carbon Intensity API with synthetic fallback"
fi
echo
echo "S3 website hosting is HTTP-only. For HTTPS, put CloudFront in front of the bucket."
