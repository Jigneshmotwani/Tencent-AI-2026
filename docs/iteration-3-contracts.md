# Iteration 3 contracts and ownership (lead-owned)

Iteration 1–2 contracts (`docs/contracts.md`) remain in force: sessions + CSRF/Origin on every mutation, `access_context`, 404 for foreign ids, SSE envelope, `telemetry.audit(...)` hash-chained, no excerpts/titles/secrets in audit or logs.

## Ownership (non-overlapping)

| Owner | Files |
|---|---|
| Lead | `backend/app/{db,migrations,config,contracts,gateway,knowledge,telemetry,localstart,bootstrap}.py`, `backend/app/audit/*`, `frontend/src/{App.tsx,main.tsx,api.ts,contracts.ts}`, `frontend/src/{ui,shell,audit}/*`, `frontend/package.json`, docs |
| W1 Workspace & Approvals | `frontend/src/workspace/*`, `frontend/src/approvals/*` |
| W2 Studio | `frontend/src/studio/*`, `frontend/src/copilot/*` |
| W3 Admin pages | `frontend/src/{tools,policies,users,budgets,connections,settings}/*` |
| W4 Backend | `backend/app/workflows/*` except `templates.py`, `backend/app/admin/*` (new), `backend/app/governance/*`, `backend/app/mcp/{api,registry}.py`, `backend/app/connections.py`, `backend/app/identity.py` (additions only), `backend/app/main.py` (router includes, page-access middleware, conversation agent-run route) |
| W5 Demo content | `mcp_servers/txn_monitoring/*`, `demo-content/*`, `backend/app/{seed,seed_defaults,prompt}.py`, `backend/app/workflows/templates.py`, `backend/app/copilot/*` |

Frontend workers use `api()` / `ApiError` / `safeError` from `frontend/src/api.ts` and put area-specific types and calls in `<area>/api.ts`. Shared types needed by two areas go to the lead.

## Data model (migration 3, applied)

- `users`: adds `email`, `title` (job label, e.g. "Compliance lead"), `status` (active|invited|disabled), `last_active_at`, `created_at`. Roles are still `capabilities` (user|builder|admin|auditor, additive: admin ⊇ builder ⊇ user).
- `groups` (id, name unique, description, used_for[] ⊂ {audiences, approvers, tool_access, budgets}) and `group_members` (group_id, user_id).
- `workflows`: adds `review_state` ('' | in_review | changes_requested) and `review_version`.
- `approvals`: `run_id` is now nullable; adds `kind` (run_action | publish_request), `payload` JSON, `approver_group_id`.
- `workflow_node_states`: adds `input` (redacted rendered config), `logs` ([{ts, level, text}]), `attempt`.
- `mcp_servers`: adds `approval_status` (approved | pending_admin_approval | rejected), `approved_by`, `notes`.
- `model_configs`: alias, label, provider, model_id, api_base, credential_ref (encrypted; never returned), input/output per 1M, enabled, is_default, allowed_groups[], source (env|admin).
- `budget_limits`: (scope_type org|group|workflow|user, scope_id) unique; limit_usd, threshold_pct, max_steps, max_tokens.
- Page access: `platform_policies` key `page_access` = `{page: {role: bool}}`.

## Demo users (existing accounts, renamed; linked source identities unchanged)

| key | username | name | title | roles | groups |
|---|---|---|---|---|---|
| priya | compliance | Priya Raman | Compliance | builder | Compliance, Onboarding |
| daniel | operations | Daniel Koh | Operations | user | Operations, Onboarding |
| wei | admin | Wei Ming Tan | Compliance lead | admin | Compliance, Compliance leads |
| aisha | auditor | Aisha Rahman | Auditor | auditor | Audit |

Groups: Compliance, Compliance leads, Operations, Onboarding, Audit. Publish reviewer group (policy `publish_reviewer_group`) = Compliance leads. Groups and roles never widen source access.

## Spec additions (W4, `workflows/spec.py`)

- `Audience`: adds `kind: 'legacy'|'only_me'|'group'|'everyone'` (default legacy = old roles/users rules) and `groups: list[str]` (group ids). `everyone` = any account with the `user` capability; `group` = member of one of `groups`; `only_me` = owner only.
- `human.approval` config adds `approver_group` (group id), `respond_within` (string), `message` (string). An approver must hold builder/admin AND be in `approver_group` when set. Requester self-approval rules unchanged.

## REST additions (all under `/api`; JSON; mutations need CSRF + Origin)

### Session and shell
- `GET /auth/me` → adds `title`, `email`, `initials`, `roles` (display order), `pages` (page keys this user may open), `demo_mode` (bool).
- `GET /auth/demo-users` → `[{key, name, title, role_label, initials}]`. Only when `DEMO_MODE=true` and the request is from loopback; otherwise 404.
- `POST /auth/demo-switch {key}` → ends the current session, creates a real session for that user, sets the cookie and returns the `/auth/me` payload. Audited `session.demo_switch`. Same gating as above.
- `GET /page-access` (any user) → `{pages:[{key,label}], roles:[user,builder,admin,auditor], matrix:{page:{role:bool}}, locked:{page:{role:bool}}, mine:[page]}`.
- `PUT /admin/page-access {matrix}` (admin) → same shape; locked cells are ignored. Audited.
- Page keys: `workspace, studio, approvals, tools, audit, budgets, policies, users, connections, settings`. Default matrix = prototype `ACCESS`; locked = prototype `LOCK`.
- API guards by page family (403 `page_forbidden`): studio → `/workflows*` mutations except runs, `/workflow-templates`, `/studio/registry`, `/workflows/*/copilot*`; approvals → `/approvals*`; tools → `/mcp*`, `/tools*`; audit → `/audit*`; budgets → `/budgets*`; policies → `/policies*`, `/admin/models*`, `/admin/page-access`; users → `/admin/users*`, `/admin/groups*`; connections → `/connections*` except OAuth callbacks. Workspace routes (`/conversations*`, `/catalog`, `/workflows/{id}/runs`, `/workflow-runs*`, `/evidence*`) stay open to the `user` capability.

### Workflows, review and versions (W4)
- `GET /workflows` items add `display_status` (draft | in_review | published | changes_requested | suspended | archived; status of the latest draft), `live_version`, `audience_label` ("Only me" | "Everyone" | group name), `owner_name`, `category` (dominant node category for the icon tile).
- `GET /workflows/{id}/review` → `{checklist:[{key,label,ok,detail}], reviewers:[{id,name,title}], live_version, draft_version, review_state, audiences:[{kind,group_id?,label}]}`. Checklist keys: `test_passed`, `writes_behind_approval`, `budget_set`, `policy_allowed`.
- `POST /workflows/{id}/submit {audience:{kind, group_id?}, note}` → `only_me` publishes immediately; otherwise creates a `publish_request` approval and sets `review_state=in_review`. Returns the workflow detail. Users keep running the live version until approval.
- `GET /workflows/{id}/versions` items add `state` (draft | in_review | published | changes_requested | superseded), `is_live`, `created_by_name`, `decided_by_name`, `audience_label`, `summary` (`{added, changed, removed}` counts vs the parent).
- `GET /workflows/{id}/versions/{v}/diff?against=live|<n>` → `{nodes:[{id,label,type,change:'added'|'changed'|'removed',detail}], edges:{added:[ids],removed:[ids]}}`.
- `POST /workflows/{id}/versions/{v}/restore` → new draft (exists).

### Approvals (W4)
- `GET /approvals?status=&kind=run_action|publish_request` items add `kind`, `payload`, `approver_group` (`{id,name}` or null), `approver_names` ([names]), `requester_name`.
- publish_request `payload`: `{version, live_version, audience:{kind,group_id,label}, note, diff, test_run:{run_id,status,latency_ms,cost_usd}|null, sources:[...], tools:[...], checklist:[...]}`.
- `POST /approvals/{id}/decision {decision:'approve'|'reject', reason}`: a publish_request approve publishes that version immutably (`workflow.publish` audit). Reject sets `changes_requested`. The requester can never decide their own publish request. Only admins or members of the reviewer group decide.
- `GET /approvals/count` counts both kinds the user can decide.

### Runs (W4)
- `GET /workflow-runs/{id}/debug` (invoker only, else 404) → `{run, steps:[{node_id,label,type,category,status,started_at,finished_at,duration_ms,attempt,usage:{model,prompt_tokens,completion_tokens,cost_usd}|null,input,output,logs:[{ts,level,text}],error:{code,message}|null,evidence:[{label,title,provider}]}], some_hidden:bool}`. Inputs/outputs are redacted; evidence excerpts are truncated; no secrets. Titles are freshly authorized for the invoker or omitted.
- `POST /workflow-runs/{id}/retry {node_id}` → `{run_id}`. Re-runs from a failed node with persisted upstream outputs, same invoker, reauthorized.
- `POST /conversations/{id}/agent-runs {workflow_id, inputs}` → `{run_id, message_id}`. Stores a user message (`@Agent · inputs`) and an assistant message with `status='agent_run'` and `run_id` = the workflow run id, so reloading the chat restores the run card.

### Admin (W4, `backend/app/admin/*`)
- `GET /admin/users` → `[{id,username,name,email,title,roles,groups:[{id,name}],sources:[provider],status,last_active_at}]`.
- `POST /admin/users {name,email,username,title,roles,group_ids,password}` → status `invited` until first sign-in.
- `PATCH /admin/users/{id} {roles?,group_ids?,title?,status?}`.
- `GET /groups` (any user) → `[{id,name,description,used_for,member_count}]`.
- `GET /admin/groups/{id}` → adds `members:[{id,name,email}]`.
- `POST /admin/groups {name,description,used_for,member_ids}`.
- `PATCH /admin/groups/{id} {description?,used_for?,add_member_ids?,remove_member_ids?}`.
- `GET /admin/models` → `[{id,alias,label,provider,model_id,api_base,has_key,input_per_1m,output_per_1m,enabled,is_default,allowed_groups,source}]`.
- `POST /admin/models`, `PATCH /admin/models/{id}`, `DELETE /admin/models/{id}`. The default model cannot be disabled or removed.
- `GET /tools` → `[{id,name,kind:'mcp'|'builtin',transport,tool_count,status,approval_status,org_enabled,added_by,tools:[{name,description,read_only,enabled,allowed_groups,input_schema}]}]`. Built-ins are Jira, plus Slack only if configured.
- `POST /mcp/servers` by a builder → `pending_admin_approval`.
- `POST /mcp/servers/{id}/approve` and `/reject` (admin).
- `PATCH /mcp/servers/{id}` accepts `tools:[{name,enabled,allowed_groups}]` and `enabled`.
- Budgets:
  - `GET /budgets/overview` → `{org:{used_usd,limit_usd,threshold_pct,resets_at}, daily:[{date,usd}]×30, top:[{name,kind,usd}]}`.
  - `GET /budgets/{groups|agents|users}` → `[{id,name,spend_usd,limit_usd,blocked,max_steps?,max_tokens?}]`.
  - `PUT /budgets/limits {scope_type,scope_id,limit_usd,threshold_pct?,max_steps?,max_tokens?}`.
  - Over-limit agents are blocked at run start (`budget_exceeded`).
- `GET /policies` adds `guardrails` (read-only P2), `approval_rules:[{action,write,requires_approval,approver_group}]`, `publish_reviewer_group`.
- Every admin change is audited (`admin.*`, `policy.*`, `budget.*`, `mcp.*`). The UI toast says "Saved · logged to audit".

### Audit (lead)
- `POST /audit/query` / `POST /audit/nl-query` events add `actor_name`, `actor_title`, `hash`, `label` (human action), `resource_label`. `GET /audit/runs/{run_id}` is unchanged.

## UI kit (lead, `frontend/src/ui`)
Import `./ui/kit` components; class names mirror `docs/design/prototype.html` (`btn`, `pill`, `tbl`, `sheet`, `segs`, `crd`, `setrow`, `ub`, …) and are defined in `ui/app.css`. Icons: `ui/icons.tsx` (lucide, 1.75 stroke). Toast: `toast('Saved · logged to audit')`. Navigation: `navigate('/studio/<id>')`, `useRoute()`.
