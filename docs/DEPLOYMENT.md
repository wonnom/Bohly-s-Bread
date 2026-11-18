# Deployment

## Deploying manually (current method)

GitHub runs the tests, and you deploy from your computer with your own AWS login.

Create a `.env.deploy` file in the project root. Git ignores this file.

```bash
AWS_REGION="your-region"
LAMBDA_FUNCTION_NAME="your-lambda-name"
```

After merging a change to `main`, run:

```bash
git checkout main
git pull
scripts/deploy.sh
```

The script stops unless you are on an up-to-date, clean `main`. It then runs the tests, builds for the Lambda's Python version and architecture, uploads the code and runs a dry-run check.

To roll back, revert the commit on `main`, then deploy again.

Complete steps 5 and 6 below once, as they also apply to manual deployment.

# Automatic deployment with GitHub Actions (optional)

Your AWS organisation currently blocks step 2, so an organisation admin must allow it before this can be used.

Pull requests run the tests. Merging to `main` runs the tests, deploys to Lambda, then runs a dry-run check.
Deployment stays switched off until the `LAMBDA_FUNCTION_NAME` repository variable exists.

## 1. Fill in your details

Run these in a terminal signed in to your AWS account:

```bash
export AWS_REGION="ap-southeast-1"          # your Lambda's region
export FUNCTION_NAME="your-lambda-name"
export ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
export REPO="wonnom/Bohly-s-Bread"
```

Check the runtime and architecture of the function:

```bash
aws lambda get-function-configuration --function-name "$FUNCTION_NAME" --region "$AWS_REGION" \
  --query '{runtime: Runtime, arch: Architectures[0], timeout: Timeout}'
```

## 2. Let GitHub sign in to AWS

This uses short-lived credentials, so no AWS keys are stored in GitHub. Skip this command if the provider already exists.

```bash
aws iam create-open-id-connect-provider \
  --url https://token.actions.githubusercontent.com \
  --client-id-list sts.amazonaws.com
```

## 3. Create the deploy role

Only the `main` branch of this repository can use this role, and it can only touch this one function.

```bash
cat > trust-policy.json <<JSON
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {"Federated": "arn:aws:iam::${ACCOUNT_ID}:oidc-provider/token.actions.githubusercontent.com"},
    "Action": "sts:AssumeRoleWithWebIdentity",
    "Condition": {"StringEquals": {
      "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
      "token.actions.githubusercontent.com:sub": "repo:${REPO}:ref:refs/heads/main"
    }}
  }]
}
JSON

cat > deploy-policy.json <<JSON
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": ["lambda:UpdateFunctionCode", "lambda:GetFunction", "lambda:GetFunctionConfiguration", "lambda:InvokeFunction"],
    "Resource": "arn:aws:lambda:${AWS_REGION}:${ACCOUNT_ID}:function:${FUNCTION_NAME}"
  }]
}
JSON

aws iam create-role --role-name bohlys-bread-github-deploy \
  --assume-role-policy-document file://trust-policy.json --query Role.Arn --output text
aws iam put-role-policy --role-name bohlys-bread-github-deploy \
  --policy-name deploy-lambda --policy-document file://deploy-policy.json
rm trust-policy.json deploy-policy.json
```

## 4. Add GitHub repository variables

In GitHub, open **Settings > Secrets and variables > Actions > Variables** and add:

| Name | Value |
|---|---|
| `AWS_ROLE_ARN` | The ARN printed by `create-role` |
| `AWS_REGION` | Your Lambda's region |
| `LAMBDA_FUNCTION_NAME` | Your Lambda's name |
| `LAMBDA_PYTHON_VERSION` | The runtime's version, for example `3.13` |
| `LAMBDA_ARCH` | `x86_64` or `arm64` |

API keys stay in the Lambda's environment variables and never go into GitHub.

## 5. Update the Lambda settings

These only need doing once:

1. Set the function timeout to **15 minutes**, the Lambda maximum, because each Gemini attempt can wait up to 5 minutes.
2. Set `GEMINI_BACKUP_MODEL` to a model your key can use, for example `gemini-3.5-flash`.
3. Confirm the handler is `main.lambda_handler`.
4. Allow the Lambda's role to read and write `seen_urls.json`, `pending_articles.json` and `code_rejections.json` in your S3 bucket.

## 6. Set the schedules

**Collect:** keep your existing 2-hour schedule. Events with no `mode` run the collect step.

**Brief:** in **EventBridge > Schedules > Create schedule**, use:

- Schedule: `cron(30 7,19 * * ? *)`
- Time zone: `Asia/Singapore`
- Target: your Lambda function
- Payload: `{"mode": "brief"}`
- Retries: 0, because the next collect run retries a failed brief

## Rolling back

With automatic deployment on, revert the commit on `main` and the workflow redeploys the previous code.
