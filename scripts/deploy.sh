#!/usr/bin/env bash
# Tests, builds and deploys the latest main branch to AWS Lambda using your own AWS login.
# Settings come from .env.deploy or the environment: AWS_REGION and LAMBDA_FUNCTION_NAME.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ -f .env.deploy ]]; then
  # shellcheck disable=SC1091
  source .env.deploy
fi

fail() {
  echo "Deploy stopped: $1" >&2
  exit 1
}

[[ -n "${AWS_REGION:-}" ]] || fail "set AWS_REGION in .env.deploy"
[[ -n "${LAMBDA_FUNCTION_NAME:-}" ]] || fail "set LAMBDA_FUNCTION_NAME in .env.deploy"

[[ "$(git branch --show-current)" == "main" ]] || fail "switch to the main branch first"
[[ -z "$(git status --porcelain)" ]] || fail "commit or discard your local changes first"
git fetch --quiet origin main
[[ "$(git rev-parse HEAD)" == "$(git rev-parse origin/main)" ]] || fail "run git pull so main matches GitHub"

PYTHON_BIN="python3"
[[ -x .venv/bin/python ]] && PYTHON_BIN="$ROOT/.venv/bin/python"

echo "Running tests..."
"$PYTHON_BIN" -m pytest -q

echo "Reading Lambda settings..."
read -r RUNTIME LAMBDA_ARCH < <(aws lambda get-function-configuration \
  --function-name "$LAMBDA_FUNCTION_NAME" --region "$AWS_REGION" \
  --query '[Runtime, Architectures[0]]' --output text)
[[ "$RUNTIME" == python* ]] || fail "expected a Python Lambda but found $RUNTIME"

echo "Building for $RUNTIME on $LAMBDA_ARCH..."
PYTHON_BIN="$PYTHON_BIN" PYTHON_VERSION="${RUNTIME#python}" LAMBDA_ARCH="$LAMBDA_ARCH" scripts/build_lambda.sh

echo "Uploading..."
aws lambda update-function-code --function-name "$LAMBDA_FUNCTION_NAME" --region "$AWS_REGION" \
  --zip-file fileb://build/lambda.zip --query LastUpdateStatus --output text
aws lambda wait function-updated --function-name "$LAMBDA_FUNCTION_NAME" --region "$AWS_REGION"

echo "Checking the deployed function..."
RESPONSE="$(mktemp)"
trap 'rm -f "$RESPONSE"' EXIT
FUNCTION_ERROR="$(aws lambda invoke --function-name "$LAMBDA_FUNCTION_NAME" --region "$AWS_REGION" \
  --cli-binary-format raw-in-base64-out --cli-read-timeout 300 \
  --payload '{"mode": "collect", "dry_run": true}' \
  --query FunctionError --output text "$RESPONSE")"

[[ "$FUNCTION_ERROR" == "None" ]] || fail "the deployed function returned an error: $(cat "$RESPONSE")"
"$PYTHON_BIN" -c "import json, sys; sys.exit(json.load(open(sys.argv[1])).get('statusCode') != 200)" "$RESPONSE" \
  || fail "the dry run did not succeed: $(cat "$RESPONSE")"

echo "Deployed commit $(git rev-parse --short HEAD) to $LAMBDA_FUNCTION_NAME."
