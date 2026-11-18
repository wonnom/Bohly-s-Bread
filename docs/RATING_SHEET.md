# Trial rating sheet setup

During the trial, the bot adds articles to a Google Sheet and sends a short Telegram alert after each brief. The client rates articles in the sheet whenever they have time.

Article briefs stay out of Telegram during the trial, so the client can't see which articles the bot selected.

## 1. Create the sheet

1. Sign in to the Google account that should own the sheet and create a blank spreadsheet at [sheets.new](https://sheets.new).
2. Name it `Bohly's Bread trial ratings`.

## 2. Add the script

1. In the sheet, open **Extensions > Apps Script**.
2. Delete the sample code, paste in the contents of [`apps_script/Code.gs`](../apps_script/Code.gs), then save.
3. Create a secret in your terminal, and keep it for steps 2.4 and 4:

   ```bash
   openssl rand -hex 32
   ```

4. In Apps Script, open **Project Settings** (the gear icon). Under **Script Properties**, add:
   - Property: `SHEET_SECRET`
   - Value: the secret you created
5. Return to **Editor**, select `setupSheet` in the function dropdown and choose **Run**.
6. Approve the permissions Google asks for. The sheet should now have five tabs.

## 3. Publish the script

1. Choose **Deploy > New deployment**.
2. Select the gear icon next to **Select type**, then choose **Web app**.
3. Set **Execute as** to **Me**.
4. Set **Who has access** to **Anyone**. The bot needs this to reach the script, and the secret blocks everyone else.
5. Choose **Deploy** and copy the **Web app URL**, which ends in `/exec`.

## 4. Update the Lambda

In **Lambda > Configuration > Environment variables**, add:

| Name | Value |
|---|---|
| `SHEET_WEBHOOK_URL` | The Web app URL ending in `/exec` |
| `SHEET_WEBHOOK_SECRET` | The secret from step 2 |
| `RATING_SHEET_URL` | The normal browser link to the Google Sheet |
| `TRIAL_START_DATE` | Day 1 of the trial in Singapore time, such as `2026-11-20` |
| `PAUSE_TELEGRAM_BRIEFS` | `true` |

Only articles first collected on or after `TRIAL_START_DATE` are added, for 7 days.

## 5. Share the sheet

Choose **Share**, add the client's Google account and give it **Editor** access. Don't use "Anyone with the link".

## 6. Deploy and test

1. Merge this change to `main` and run `scripts/deploy.sh`.
2. In the Lambda **Test** tab, run `{"mode": "collect"}`, then `{"mode": "brief"}`.
3. Check that rows appear in **Rate articles** and the alert arrives in Telegram.

## Resuming Telegram briefs

Remove `PAUSE_TELEGRAM_BRIEFS` or set it to `false`. No redeploy is needed.

## Changing the script later

After editing the script, choose **Deploy > Manage deployments**, edit the deployment and select **New version**. This keeps the same URL.
