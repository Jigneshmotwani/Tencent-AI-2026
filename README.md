# Permission-aware Knowledge Foundation (Tencent AI 2026) — iteration 1

Product name: **[Product Name]** (placeholder, to be chosen).

Local React/TypeScript + FastAPI + PostgreSQL app. Real, read-only delegated adapters for Google Drive and Confluence; all assistant inference uses a local LiteLLM proxy. No simulated source or canned business answer. No inference credential is bundled. Studio and later connectors are unavailable.

## Start on this Mac

Prerequisites: Python 3.11–3.13, uv, Node 20.19+ or 22.12+, PostgreSQL tools (already installed here). From this directory:

```sh
./scripts/start.sh
```

Open **http://localhost:5173** (use `localhost`, consistently). The command installs locked workspace dependencies when necessary, bootstraps a local PostgreSQL cluster on `127.0.0.1:55432`, separate `app`/`litellm` databases, four named accounts, migrations, LiteLLM on `127.0.0.1:4000`, a local OTLP receiver on `127.0.0.1:4318`, and API on `127.0.0.1:8000`. Ctrl-C stops native services; data persists under ignored `.local/`. Run from a terminal that permits local sockets and network access for first-time package/Prisma downloads. `PG_BIN=/path/to/postgres/bin ./scripts/start.sh` selects installed PostgreSQL tools. On Windows (Git Bash) there is usually no native PostgreSQL: use `./scripts/start.sh --services compose` with Docker running. Ports are configurable in `.env` (`LOCAL_PORT_APP`, `LOCAL_PORT_API`, `LOCAL_PORT_GATEWAY`, `LOCAL_PORT_COLLECTOR`, `LOCAL_PORT_DB`; defaults 5173/8000/4000/4318/55432); OAuth redirect URIs and `APP_ORIGIN`/`API_ORIGIN` follow the chosen API/app ports. This host's missing Homebrew share/lib prefix is accommodated through workspace-local executable copies/symlinks; no system files are changed.

If Docker is running instead, use `./scripts/start.sh --services compose`; Compose starts only local PostgreSQL, with the API/proxy/collector/UI still supervised locally. Stop that supporting database with `docker compose down` (omit `-v` to retain data). Do not switch database profiles without preserving the chosen data directory/volume.

The launcher creates ignored mode-600 `.env` encryption/proxy/database secrets and mode-600 `.local/demo-accounts.txt` account credentials. Read that file privately; never paste credentials into chat. Account names: `operations` (User), `compliance` (Builder + User), `admin` (Admin + Builder + User), `auditor` (audit metadata only). Optional bootstrap password fields in `.env.example` apply only when creating a new account. There is no public signup. Losing `ENCRYPTION_KEY` requires reconnecting source accounts; keep it with the local data.

`npm --prefix frontend run build` runs TypeScript and frontend build. `GET http://localhost:8000/api/health` reports secret-free readiness. No test files or persistent verification scripts are supplied.

## Configuration and OAuth

The launcher initially derives `.env` from `.env.example`; edit `.env` locally and restart. Never use `VITE_` for secrets. Browser cookies are HttpOnly, SameSite=Lax, server-expiring/revocable, with Origin + CSRF checks on mutations. `COOKIE_SECURE=false` is the documented localhost HTTP exception; use Secure cookies with HTTPS if operating outside this local demonstration. Services must remain on loopback. Use separate browser profiles for simultaneous user identities, or sign out/in explicitly.

Google: create a Web application OAuth client in a dedicated Google project, enable Drive API, configure the consent screen and designated demo users. Set `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_FOLDER_ID`. Exact authorized redirect URI:

```text
http://localhost:8000/api/connections/drive/callback
```

Requested scopes: `https://www.googleapis.com/auth/drive.readonly`, `openid`, `https://www.googleapis.com/auth/userinfo.email`. Authorization code + S256 PKCE; offline consent. This read scope may require Google's verification or an appropriately configured restricted demo consent audience. Search includes the selected root and up to five delegated-visible direct child folders (ordered by name). Reads independently verify root or direct child-folder membership and permission. Deeper nesting and shortcuts are excluded. Google Docs export, UTF-8 text and text-based PDF reads are supported; OCR is unavailable. Link each app user's own source account in Knowledge.

Atlassian: create an OAuth 2.0 (3LO) integration in the developer console, enable Confluence scopes and identity access, set `ATLASSIAN_CLIENT_ID`, `ATLASSIAN_CLIENT_SECRET`, `CONFLUENCE_SPACE_KEY`, and preferably `CONFLUENCE_CLOUD_ID` (mandatory to resolve multiple eligible sites). Exact callback:

```text
http://localhost:8000/api/connections/confluence/callback
```

Requested scopes: `search:confluence`, `read:page:confluence`, `read:space:confluence`, `read:confluence-user`, `read:me`, `offline_access`. Confidential-client authorization code flow; Atlassian's current documented flow does not describe PKCE. Current CQL search and v2 page/space reads are used; dedicated `*.atlassian.net` sites supported. Provider permissions must allow the current user's content reads. State expires after ten minutes, is session/owner/provider bound and consumed atomically. Reconnect replaces that user's credentials; disconnect deletes local credentials/state and blocks new requests.

Model: set `MODEL_ID` to a provider/model identifier actually supported by your authorized LiteLLM provider, `MODEL_API_KEY`, and `MODEL_API_BASE` only if that provider requires one. `MODEL_ALIAS` defaults to `knowledge-model`. No provider or model is silently substituted. No Tencent credential was found, and Tencent inference has **not** been verified. CodeBuddy/WorkBuddy credits are not proof of model API entitlement. The model must support Chat Completions streaming; final usage is recorded if returned. Proxy pricing estimates use the pinned LiteLLM package pricing map and are labelled estimated; absent usage/prices are unknown/null. Custom provider pricing remains unknown unless supplied/verified in a later accepted configuration. No hard budgets or autonomous loops.

## Dedicated synthetic source setup

All documents in `demo-content/` are **synthetic**. Manually import the shared `drive-*.txt` checklist and summary as UTF-8 text files or Google Docs into the configured root folder; put the restricted review memo into one direct child folder with Google limited access enabled; create the shared `confluence-approved-onboarding.md` page in the configured dedicated space. `confluence-restricted-compliance.md` needs native page restrictions, which Confluence Cloud's Free plan does not provide; create it only on a paid plan, otherwise the restricted-access demonstration runs through Drive only. The app never uploads source content or edits remote permissions. Configure sharing/restrictions in those source systems using the chosen real accounts:

| Source material | Operations source identity | Compliance source identity | Platform Admin source identity |
| --- | --- | --- | --- |
| Drive checklist + submitted summary | Read | Read | Only if explicitly granted at source |
| Confluence approved procedure | Read | Read | Only if explicitly granted at source |
| Drive restricted review memo | No access | Read | No extra access from platform role |
| Confluence restricted compliance procedure (paid plan only) | No access | Read | No extra access from platform role |

Use a restricted child page/file with actual native sharing, not a title label alone. Share the Drive root with operations and compliance. Enable native limited access on the direct child folder containing the restricted memo, grant only compliance to that folder, and keep it within the first five visible child folders. Confirm operations cannot read that memo in Drive itself. If using a paid Confluence plan, apply native page view restrictions to the restricted page and confirm operations cannot open it directly. Broad inherited sharing must not grant the restricted material. This matrix is external provisioning documentation, never platform ACLs.

Demo: sign in as operations; connect own Drive and Confluence; ask **“Why is Acme Trading's onboarding blocked, and what should I do next?”**; follow up **“Which document supports that, and who should I contact?”**; open citations and Activity. Ask for the restricted review and confirm only accessible material is used. Repeat in the compliance profile with real native source grants. Admin starts without any connections.

## Limits and references

No global content cache, embeddings, synchronization, OCR, source writes, public hosting, Agent Studio, policy/budget editor, MCP/Jira/Slack. Source excerpts live only in request memory and fresh source responses; conversations intentionally retain generated answers. Prior assistant answers/excerpts never become model context for a new run. Authorization cannot make already displayed information unseen. The source reader indicates a changed content hash. Retrieval is bounded to one page per enabled source, eight total documents, 6,000 characters/document, 2 MB/read, 25 seconds/source and 1,200 output tokens by default. One API process enforces four active runs and one active run/conversation; cancellation usage can be incomplete. This is a local hackathon build, without a production security/operations claim.

Official references checked during implementation: [Google search](https://developers.google.com/workspace/drive/api/guides/search-files), [Google web OAuth](https://developers.google.com/identity/protocols/oauth2/web-server), [Atlassian 3LO](https://developer.atlassian.com/cloud/confluence/oauth-2-3lo-apps/), [Confluence v2 pages](https://developer.atlassian.com/cloud/confluence/rest/v2/api-group-page/), [LiteLLM spend tracking](https://docs.litellm.ai/docs/proxy/cost_tracking), [OTel sensitive data](https://opentelemetry.io/docs/security/handling-sensitive-data/).

See [architecture](docs/architecture.md) and [iteration handoff](docs/iteration-1-handoff.md).
