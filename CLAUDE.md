# Client Files Viewer v2

Replaces the PowerShell script `Lab_Client_Assessments_Backupv2.ps1` at
AmericaWorks NYC labs. Discovers PDF client assessments on 24 lab PCs,
classifies + renames them, and (on commit) copies to a network share and
deletes the source.

## Stack

- **Backend:** Python 3.12 · uv · FastAPI · smbprotocol · pypdf · **Postgres** (`clientfiles_v2` on 192.168.70.10:5432 — async via SQLAlchemy+asyncpg for auth code, sync via psycopg for scan/commit/archive). Alembic owns migrations.
- **Frontend:** Next.js 16 (App Router) · TypeScript · Tailwind v4 · Radix primitives (no shadcn)
- **Design reference:** `docs/Designs/Demo.jpg` — DocFlow Pro aesthetic (dark navy sidebar, blue accent, dashboard widgets, professional DMS)
- **Design + build plan:** `docs/PLAN.md` · **Archive/restore:** `docs/ARCHIVING_PLAN.md` (shipped 2026-08-05) · **Auth:** `docs/PHASE15_AUTH.md` (in progress) · **Entra SSO:** `docs/ENTRA_SSO.md` (spec, not started)

## Layout

```
client-files-viewer-v2/
  api.py           FastAPI wrapper
  scan.py          discover + classify + index (CLI + generator)
  commit.py        copy → verify → delete (CLI + generator)
  archive.py       archive/restore service (bulk + streaming, background thread)
  repair.py        reconcile pdfs.archive_* columns against SMB reality
  share.py         shared SMB helpers (active-folder walker, rename with retry)
  classify.py      PDF text / filename regex classifier
  db.py            psycopg sync connection + upsert
  unlock.py        remote taskkill for Adobe locks
  bulk.py          bulk delete generator
  pcs.py           PC → IP map (24 entries)
  scheduler.py     asyncio scheduler loop (auto scan/commit)
  auth/            Phase 15 auth: routes, service, audit, permissions, notifications
                   + sso/sso_routes (Entra), mfa/mfa_routes/totp (2FA), app_settings/settings_routes (runtime toggles)
  backend/alembic/ migrations (schema owned by Alembic, not app code)
  scripts/         smokes + one-off tools (backfill_share, repair_archive_state, …)
  .env             SMB + DB creds (gitignored)
  frontend/        Next.js app
  docs/            PLAN.md, SPEC.md, ARCHIVING_PLAN.md, PHASE15_AUTH.md, ENTRA_SSO.md, Designs/
```

## Commands

```powershell
# API (backend)
uv run --env-file .env uvicorn api:app --reload --host 0.0.0.0 --port 8000

# CLI equivalents
uv run --env-file .env scan.py
uv run --env-file .env commit.py

# Frontend
cd frontend
pnpm dev
```

## Conventions

- **Ponytail mode is on.** Lazy = efficient. First rung of the ladder that works wins.
- **Match the design in `docs/Designs/Demo.jpg`.** Don't invent a different aesthetic; refine within it.
- **`ponytail:` comments** mark deliberate simplifications with the upgrade path.
- **No new backend deps** unless a few lines can't replace them.
- **No shadcn.** Custom components on Radix primitives + Tailwind.
- **Real SMB, not mocks.** Test against the actual 24 PCs.
- **Auth is live (Phase 15 M1 + M2 audit + M3 MFA + Entra SSO).** Bootstrap admin: `admin@aw.local` / `Correct-Horse-Battery-9!`. Four roles: `admin`, `operator`, `corporate_rep`, `viewer`. Permissions per role in `auth/permissions.py`. **Corporate Rep = viewer + Salesforce push** (`salesforce:read`/`salesforce:push`), no run triggers — the role for reps who file assessments into Salesforce.
- **Salesforce push** (`docs/SALESFORCE_INTEGRATION.md`): send a committed assessment PDF onto a client's Salesforce Person Account. `sf.py` is a JWT-bearer client (server-to-server, reuses PyJWT). Flow: case number → `Assignment__c.Name` → `Participant__c` (Person Account) → upload a `ContentVersion` with title-based dedupe. Learned client↔case#↔account mapping in `sf_client_map` prefills repeat clients. Endpoints `/api/salesforce/{status,prefill,resolve}` + `POST /api/pdfs/{id}/salesforce` (audited `SALESFORCE_PUSH`); UI in the PDF drawer, per Files row, and a blue bulk button in the Files toolbar. **Name-match safeguard:** push refuses to file when the PDF's client name doesn't match the resolved account, unless `override_name_mismatch` (UI "Send anyway"). **Currently pointed at the sandbox** (`SF_*` env); **production cutover runbook** in `docs/SALESFORCE_PROD_SETUP.md` (step-by-step: External Client App "Client Assessment Files Viewer", `Integration` role, standard-license `Minimum Access` integration user, `CFV Salesforce Integration` permission set, IP lockdown) + security checklist in `docs/SALESFORCE_INTEGRATION.md`. Signing key lives in gitignored `secrets/`.
- **Scan-time content dedupe:** `scan.py` skips indexing a PDF whose `md5`/`text_hash` already matches a non-archived row (source left in place), so duplicate downloads never become committed rows. `scripts/prune_content_dupes.py` cleans pre-existing dupes and never deletes a `dest_path` still referenced by another row.
- **Two sign-in front doors:** `/login` is Entra SSO-only (primary); `/admin/login` is the local email+password form (break-glass / non-Microsoft accounts). Forgot-password is local-only.
- **App 2FA (TOTP + email OTP via Resend + backup codes)** in `auth/mfa.py` / `auth/mfa_routes.py`. Global on/off via the `mfa_required` runtime setting; per-user `users.mfa_exempt`; admin reset at `POST /api/v1/users/{id}/mfa/reset`. Enrolment/challenge UI at `/mfa`.
- **Runtime settings** (admin-flippable, no restart) live in the `app_settings` table via `auth/app_settings.py` + `/api/v1/settings` (perm `system:read`/`system:write`). Keys: `sso_login_enabled` (off = local-only), `sso_allowlist_enabled`, `mfa_required`. Surfaced in the Settings page "Access & Security" card.
- **SSO allowlist:** when `sso_allowlist_enabled` is on, only emails in the `sso_allowlist` table may sign in via Entra (checked in the SSO callback for new *and* existing users; not allowed → `/login?sso_error=not_allowed` "contact IT Support"). Managed on the **Security page (`/admin/security`, nav "Security", perm `system:write`)** — search + pagination + bulk add — via `/api/v1/settings/sso-allowlist` (`GET ?q&limit&offset`, `POST {emails:[…]}` bulk, `DELETE /{email}`). Local `/admin/login` accounts are exempt.
- **Access & Security settings** (SSO on/off, allowlist, Require-2FA) live on the dedicated **`/admin/security`** page, not the scheduler-focused `/settings` page.
- **Email:** `auth/emails.py::send_mail` prefers **Resend** (HTTP API, `RESEND_API_KEY`) and falls back to SMTP. Both are blocking — `await anyio.to_thread.run_sync(send_mail, ...)` in async code.
- **Audit lock rule:** `auth/audit.py::emit` takes a per-write advisory lock. Always call `emit(None, ...)` (its own committed tx) — never pass the request `conn`. Passing the request connection holds the lock for the whole request and self-deadlocks any later `emit(None)` in that request (and poisons the lock process-wide). The only exception is a dedicated, immediately-committed connection.

## Explicitly out of scope (until asked)

- All v1 sprawl: Chat, AI Analysis, Favorites, Trash, Sessions, Activity Log widget bloat
- Cloud deployment (this runs on the LAN server)

(Basic **mobile responsiveness shipped** — off-canvas sidebar + hamburger, topbar/grid reflow at ≤768px; still desktop-first, so deep mobile polish stays low priority.)

## When continuing work

Read `docs/PLAN.md` first — it's the source of truth for what's done, in progress, and next. For feature-specific work, prefer the feature's dedicated plan (`docs/ARCHIVING_PLAN.md`, `docs/PHASE15_AUTH.md`) — those have a "What actually shipped" section at the top for anything that's landed, plus deferred / gotcha notes.

## SSE + BaseHTTPMiddleware gotcha

Streaming responses through `BaseHTTPMiddleware`-based middleware (Csrf, SecurityHeaders, RequestId — all three in the stack) get batched via anyio memory streams; frames flushed at end when yields are slow. Workarounds already applied in `api.py::_archive_stream_sse`: `X-Accel-Buffering: no`, `Cache-Control: no-cache, no-transform`, 2KB pad on first flush, 15s keepalive. If a new SSE endpoint yields slowly and stalls anyway, install `sse-starlette` and use `EventSourceResponse` (or convert the three middlewares to pure ASGI).
