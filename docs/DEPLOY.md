# Deploying Internal Brain: Vercel + Render + Grafana Cloud

```
Browser ──► Vercel (frontend/, static React app)
              └─ /api/* proxied by frontend/vercel.json ──► Render web service (Dockerfile)
                                                              ├─ FastAPI API   (public, $PORT)
                                                              ├─ LiteLLM gateway (127.0.0.1 only)
                                                              └─ OTel Collector (127.0.0.1 only, redacts, forwards to Grafana Cloud)
                                                           Render Postgres (app data + LiteLLM tables)
Grafana Cloud ◄── traces + metrics from the collector;  ──► reads usage/audit metadata from Postgres as the read-only `grafana_ro` role
```

The browser only ever talks to the Vercel URL. That keeps the session cookie first-party and is why `APP_ORIGIN` must be your Vercel URL.

**Cost to expect** (check render.com/pricing): web service `standard` ≈ $25/month (2 GB RAM; measured about 800 MB at idle, so the 512 MB plans, including Free and Starter, cannot run it) + Postgres `basic-256mb` ≈ $6/month. Vercel Hobby and Grafana Cloud Free are $0.

## Before you start

- The code must be on GitHub (Vercel and Render both deploy from a repo). Commit and push this branch first.
- Have ready: a model API key and a verified LiteLLM model string for `MODEL_ID` (for example `openai/<provider model id>`).
- Optional, for the data connectors: Google, Atlassian and Slack OAuth apps (step 5).

## 1. Vercel (frontend)

1. Vercel dashboard → **Add New → Project** → import your repo.
2. **Root Directory: `frontend`**. Framework: Vite (detected). Build settings come from `frontend/vercel.json`.
3. Name the project, for example `internal-brain`. Your URL will be `https://internal-brain.vercel.app`. **Deploy.**
4. Copy the production URL. You need it in step 2. If it is not what you expected, use whatever Vercel shows.

The site will load but show errors until the backend exists. That is expected.

## 2. Render (API + database)

1. Render dashboard → **New → Blueprint** → pick the repo → it reads `render.yaml`.
2. Fill the three prompts:
   - `APP_ORIGIN`: your Vercel URL from step 1, no trailing slash.
   - `MODEL_ID` and `MODEL_API_KEY`.
3. **Apply.** The first build takes several minutes.
4. When it is live, open `https://<your-service>.onrender.com/api/health`. You want `"database":"ready"`, `"gateway":"ready"`, `"collector":"ready"`.
5. **Point Vercel at the backend.** `frontend/vercel.json` assumes the service URL is `https://internal-brain-api.onrender.com`. If Render gave you a different hostname (for example with a suffix), edit the one `destination` line in `frontend/vercel.json`, commit and push. Vercel redeploys.
6. **Back up two secrets now.** In the service's **Environment** tab copy `ENCRYPTION_KEY` into your password manager. It encrypts every stored OAuth token, and losing it means everyone must reconnect their accounts. Do not delete or regenerate it.

## 3. Sign in

Open the Vercel URL. The built-in accounts are `admin`, `compliance`, `operations` and `auditor`. Their passwords are the `BOOTSTRAP_*_PASSWORD` values in the Render **Environment** tab. Share them only with the people who should have those roles. The values are applied once, when the account is first created, so changing the variable later does not change an existing account's password.

## 4. Check it works

- Signing in succeeds and the page does not bounce back to the login form. If it does, see Troubleshooting.
- Audit → **Verify integrity** says ok (as `admin` or `auditor`).
- Ask a question in Workspace. Without connectors it answers that it found nothing, which still proves the model and gateway work.
- Run an agent from Studio and watch the live steps. They stream over the Vercel proxy, so test the longest run you plan to demo. Vercel's proxy limit on Hobby was 120 seconds when I checked; verify it for your plan.

## 5. Connect Google, Atlassian and Slack (optional)

Add each redirect URI exactly. They are also printed in the Render service logs at startup.

| Provider | Redirect URI(s) | Render env vars |
|---|---|---|
| Google Drive | `<VERCEL_URL>/api/connections/drive/callback` | `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_FOLDER_ID` |
| Atlassian | `<VERCEL_URL>/api/connections/confluence/callback` and `<VERCEL_URL>/api/connections/jira/callback` | `ATLASSIAN_CLIENT_ID`, `ATLASSIAN_CLIENT_SECRET`, `CONFLUENCE_SPACE_KEY`, `CONFLUENCE_CLOUD_ID`, `JIRA_PROJECT_KEYS` |
| Slack | `<VERCEL_URL>/api/connections/slack/callback` | `SLACK_CLIENT_ID`, `SLACK_CLIENT_SECRET`, `SLACK_TEAM_ID` (and `SLACK_CHANNELS`, `SLACK_WRITE_CHANNELS` if needed) |

Scopes are in the README ("OAuth setup"). Saving environment variables restarts the service. Then each user opens **Connections** and connects their own accounts.

Google note: `drive.readonly` is a restricted scope. While your OAuth consent screen is in **Testing**, only the test users you list can connect, and their grants expire after about a week. Publishing needs Google's verification.

To load demo data or a demo pack, open the service's **Shell** tab in Render and run the commands from the README ("Synthetic demo content and seeding", "Demo packs"), for example `python -m backend.app.demo_pack list`. Seeding as a user needs that user to have connected their accounts first.

## 6. Grafana Cloud

1. Create a free stack at grafana.com.
2. **Send telemetry.** Stack → **Connections → OpenTelemetry (OTLP)**: note the OTLP endpoint URL and the numeric instance ID, and create a token. Set on Render:
   - `GRAFANA_OTLP_ENDPOINT` (for example `https://otlp-gateway-prod-eu-west-0.grafana.net/otlp`)
   - `GRAFANA_OTLP_USER` (the instance ID)
   - `GRAFANA_OTLP_TOKEN`
3. **Create a service account** (Administration → Users and access → Service accounts) with the **Admin** role and generate a token.
4. **Find the database host.** Render → your database → **Connect → External** → copy the hostname. Check Render's database access settings allow connections from Grafana Cloud (its IP ranges are listed in Grafana's docs under "allow list").
5. **Run the setup script** from your laptop in this repo. `--pg-password` takes the `GRAFANA_DB_PASSWORD` value from the Render Environment tab:
   ```sh
   uv run --frozen python deploy/grafana_cloud_setup.py \
     --url https://<your-stack>.grafana.net --token <service-account-token> \
     --pg-host <external db hostname> --pg-password <GRAFANA_DB_PASSWORD>
   ```
   It creates the Postgres datasource, imports the three dashboards, creates the scope-denials alert and points them at your stack's Prometheus and Tempo datasources. Add `--dry-run` to preview.
6. The script prints two values. Set them on Render: `GRAFANA_URL` and `GRAFANA_TEMPO_UID`. They make the "open trace" links in the app work.
7. Use the app for a few minutes, then open the dashboards. Metrics and traces take a minute or two to appear.

Grafana Cloud requires sign-in, unlike the local anonymous Grafana. Share dashboard access only with people who may see usernames and usage.

## Precautions

- **Never use Render's `free` Postgres for data you want to keep.** It is deleted 30 days after creation. `render.yaml` uses `basic-256mb`.
- **Vercel Hobby is for personal, non-commercial use.** Check Vercel's terms for your situation.
- **The audit anchor** (`.local/audit-anchor.json`) lives on the container's disk, so it resets on each deploy. The hash chain in the database still verifies; only "the end of the log was cut off" detection restarts from the next verify.
- **Do not set `DEMO_MODE`.** It only works on loopback.
- **A custom domain on Vercel** changes the origin. Update `APP_ORIGIN` and every OAuth redirect URI.
- **Model spend:** set org and agent budgets in the app (Budgets) and a hard limit at your model provider.
- **Keep secrets out of Git.** Everything sensitive lives in Render's Environment tab.
- **Login throttling** is per client address and username. Behind two proxies all users share an address, so repeated bad logins for one username can briefly lock that username (about a minute).

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `Invalid host header` / HTTP 400 from the API | The Host is not allowed. `RENDER_EXTERNAL_HOSTNAME` is added automatically; for any other hostname, set `ALLOWED_HOSTS` on Render. |
| `origin_rejected` (403) on sign in | `APP_ORIGIN` does not exactly equal the URL in the browser. Fix it, or sign in from that exact URL. |
| Sign in succeeds, then you are sent back to the login form | The `/api` rewrite is not reaching Render (check the `destination` in `frontend/vercel.json`) or `COOKIE_SECURE` is not `true`. |
| API restarts in a loop or Render shows "out of memory" | The plan is too small. Use `standard` (2 GB) or larger. |
| `redirect_uri_mismatch` from a provider | The redirect URI in the provider's console must match the table above exactly. |
| Health shows `"gateway":"unavailable"` | LiteLLM is still starting (give it a minute) or crashed; read the service logs. |
| Startup log: `could not create database role` | The database user may not create roles. The app still runs (as the database owner) and the audit table's append-only trigger still applies. The Grafana Postgres panels need the `grafana_ro` role, so create it with a database user that has the CREATEROLE privilege and restart the service. |
| Dashboards are empty | Wait a few minutes; check the three `GRAFANA_OTLP_*` values; the Render log line `Telemetry:` says whether forwarding is on. If only some panels are empty, the metric names in Grafana Cloud may differ from the local Prometheus ones; check them in Explore. |

## Updating

Push to the branch Render and Vercel watch. Both redeploy automatically (`autoDeploy` is on for Render). Schema migrations run on every start and are idempotent.
