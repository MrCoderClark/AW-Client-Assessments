# Phase 15 — Authentication Platform

**Status:** M1 shipped + M2 ✅ complete · **Owner:** Joe · **Last updated:** 2026-08-03

Three-part document: **SRS** (what it must do) → **TDD** (how it works) → **Implementation plan** (build order, milestones).

**Rev v2 change:** DB is a **new Postgres database** `clientfiles_v2` on the existing server `192.168.70.10:5432`. Fresh schema, no v1 sharing → no dual-writer risk. Migrations are **Alembic + SQLAlchemy Core** owned by v2. Prisma at v1 is untouched.

**Phase 0 decisions (locked 2026-08-03):**
- DB name: `clientfiles_v2`
- Schema tool: SQLAlchemy 2.0 Core (schema DSL only) + Alembic (migrations); Python queries stay raw via SA Core `text()` or SA Core select expressions — **no ORM classes, no hot-path ORM sessions**
- Role names: `admin`, `operator`, `viewer`
- v1 world: **N/A** — separate DB, v1 keeps its own `clientfiles` DB untouched

---

## Progress snapshot — 2026-08-03

M1 (**auth spine**) is complete end-to-end and Postman-testable. **M2 is complete** — branches 29 (audit log with hash chain), 30 (rate-limit + lockout), 31 (correlation IDs + structured logs), 32 (admin user endpoints), and 33 (admin `/users` UI) all shipped.

### Shipped branches (chronological)

| # | Branch | What landed |
|---|---|---|
| Phase 0 | Setup | Alembic scaffolded on `backend/alembic/`; DB `clientfiles_v2` created; env.py hard-fails without `DATABASE_URL`; initial migration `89986958c76a` covers 19 tables + 3 enums for the whole M1–M5 auth surface. |
| 22 | `auth-service-and-db-plumbing` | `auth/engine.py` (SA async engine + `transaction()` + `get_conn` FastAPI dep + `dispose()`), `auth/settings.py` (env-driven `AuthSettings`), `auth/random.py`, `auth/context.py` (`AuthContext` + `ANONYMOUS`), `auth/permissions.py` (role→permission map for admin/operator/viewer), service stubs for password/token/session/auth. |
| 23 | `auth-tokens-jwt-refresh` | Ed25519 JWT (PyJWT); `AccessClaims` dataclass; refresh tokens = 256-bit URL-safe; HMAC-SHA256 refresh hash (server-secret keyed) — **divergence from design §B.5's Argon2 suggestion**: SHA can be indexed for O(1) session lookup, which Argon2's random salt precludes. Documented at `auth/tokens.py:14-18`. `auth/token_state.py` for jti-revoke + session-state DB checks. `scripts/gen_jwt_key.py` + `scripts/token_smoke.py` (10 checks). |
| 24 | `auth-endpoints-and-openapi-errors` | `auth/passwords.py` (Argon2id + dummy-hash for constant-time login), `auth/sessions.py` (full lifecycle + nuclear per-user revoke on refresh reuse — safer than per-family CTE), `auth/service.py` (`AuthService`, `TokenPair`, `MePayload`), `auth/middleware.py` (Bearer → `AuthContext`), `auth/errors.py` (RFC 9457 problem+json handlers), `auth/deps.py` (service singletons + `DbConn`), `auth/routes.py` (`/login`, `/logout`, `/logout-all`, `/refresh`, `/me`, `/jwks`). Config: `docs_url="/api/docs"`, `openapi_url="/api/openapi.json"`. `scripts/create_admin.py` + `scripts/endpoints_smoke.py` (9 checks). **Refresh-reuse writes committed via separate tx** so `raise RefreshInvalid` doesn't roll back the nuclear revoke — `auth/service.py:_commit_reuse_security`. |
| 25 | `rbac-decorator-on-existing-endpoints` | `require(perm)` FastAPI dep on every Phase 1–11 route in `api.py` (`/api/pdfs`, `/api/pcs/*`, `/api/scans`, `/api/commits`, `/api/schedule`, `/api/logs`, `/api/runs`, `/api/pdfs/bulk`). Operator role got `pc:write` for rename/delete of the file explorer. Bulk endpoint has inline `run:trigger` (commit) / `pdf:delete` (delete) split. `scripts/rbac_smoke.py` (18 checks). |
| 26 | `frontend-login-and-guarded-routes` | Next.js proxies `/api/*` → `:8000` via `next.config.ts` rewrite (same-origin so refresh cookie works, zero CORS drama). `app/_lib/auth.ts` — token store (memory only, never localStorage per §FR-AUTH-14), `apiFetch` with silent refresh + one retry, in-flight dedupe on concurrent 401s. `app/_components/auth-provider.tsx` (React context, bootstrap silent-refresh, route guard). `app/login/page.tsx`. `app/_components/app-shell.tsx` (renders shell only when authenticated, boot spinner, public routes bare). Topbar avatar → user menu w/ role + Sign out. **PDF viewer fetch→blob→iframe** because `<iframe src>` can't send `Authorization`. Same fix for the file explorer's Download button. All 7 previously-existing pages migrated from raw `fetch` to `apiFetch`. |
| 26.5 | **SQLite → Postgres port** (not a numbered branch — driven by user request) | Alembic migration `6c342edcdb59` created `pdfs`, `pc_status`, `scan_runs`, `schedule` in Postgres with native types (TIMESTAMPTZ, JSONB, BIGSERIAL, real BOOLEAN). `db.py` rewritten on psycopg (sync, `dict_row`); public API unchanged so scan/commit/scheduler didn't need refactoring. Callers ported: `?` → `%s`, `datetime('now')` → `NOW()`, `json.loads(counts_json)` dropped (JSONB auto-decodes). `scripts/migrate_from_sqlite.py` (idempotent). `data.db` retired but preserved on disk; added to `.gitignore`. **Scheduler pinned to `America/New_York`** via `zoneinfo.ZoneInfo` and `SET TIME ZONE` on every psycopg connection (override via env `APP_TIMEZONE`). |
| 27 | `invite-flow-and-email-verify` | `auth/emails.py` (SMTP reusing Phase 7 config; 4 plaintext templates: invite/verify/reset/changed). New service methods: `accept_invite`, `verify_email`, `request_password_reset`, `complete_password_reset`, `change_password`; new errors `InvalidToken` + `PasswordInvalid` + `PasswordComplexityError` all mapped to 400 problem+json. New endpoints: `POST /password/{forgot,reset,change}`, `POST /email/verify`, `POST /accept-invite` (auto-logs-in on success via public `AuthService.issue_login`), `POST /users` (admin invite, `user:invite` perm). Frontend: 4 new public pages (`/accept-invite`, `/verify-email`, `/forgot-password`, `/reset-password`) + shared `auth-card.tsx`. "Forgot password?" link on login. `PUBLIC_ROUTES` updated in provider + shell. Client helpers in `auth.ts`. `scripts/invite_user.py` + `scripts/invite_flow_smoke.py` (11 checks). |
| 28 | `security-headers-cors-csrf` | `auth/http_security.py` — `SecurityHeadersMiddleware` (CSP, X-Content-Type-Options, X-Frame-Options=DENY, Referrer-Policy=strict-origin-when-cross-origin, Permissions-Policy locking down geo/mic/cam, HSTS only when `X-Forwarded-Proto: https`) + `CsrfMiddleware` (double-submit; sets `cfv_csrf` cookie on safe reqs; requires `X-CSRF-Token` header on unsafe non-Bearer reqs; exempts `/api/v1/auth/*` to solve login/refresh bootstrap and `/api/health`+`/api/docs`). CORS: `allow_origins=*` replaced with env `ALLOWED_ORIGINS` (default `http://localhost:3000`) + `allow_credentials=True`. Refresh cookie `Secure` flag from `AUTH_COOKIE_SECURE` env. Frontend `apiFetch` reads `cfv_csrf` cookie + echoes to header on unsafe non-Bearer. `scripts/_check_headers.py`. |
| 33 | `admin-users-page` | Frontend for the M2 admin surface. New sidebar entry **Users** (gated on `user:read`, so operator/viewer never see it). `frontend/app/_lib/admin-users.ts` — typed client for the 7 admin endpoints + the existing invite endpoint. `frontend/app/admin/users/page.tsx` — one file, list + drawer + invite modal (ponytail: they're tightly coupled so keeping them together reads better than three files). List: search box (server-side `q` filter), status chip row (All / Active / Invited / Suspended), role dropdown, "Show deleted" checkbox, refresh button, invite button (gated on `user:invite`). Table renders avatar-initials, name/email, role pill, status pill, MFA state, last-login relative, created relative. Row click opens the drawer. Drawer (Radix Dialog styled with existing `.drawer`): profile edit inline (first/last/display/email/role) with a Save/Cancel toggle; activity panel (last login, failed attempts, locked-until, email-verified, created, suspended reason, deleted date); action buttons contextual to `u.status` — Suspend or Reactivate, Force password reset (`confirm()`), Soft-delete (or Permanently delete when already soft-deleted). Force-reset shows the mailed status or the URL to hand off manually. All mutations reload the drawer + list. Invite modal (Radix Dialog styled with existing `.modal`): email, first, last, role select; on success shows the invite URL (with a Copy button) whether mail_ok or not. Sidebar `active` matching updated to also match `startsWith(href + "/")` so nested admin routes highlight the parent. `IconUsers` added to icons.tsx. No new deps. Type-check clean (`node_modules/.bin/tsc --noEmit`, exit 0). Backend smokes (admin_users_smoke, rate_limit_smoke, request_id_smoke, audit_smoke) all still pass. **Frontend UI check with real backend not yet run — user should spin up `pnpm dev` and click through.** |
| 32 | `admin-user-endpoints` | `auth/admin_routes.py` — new router at `/api/v1/users/*`, backend for the M2 admin surface. Endpoints: `GET /users` (filters: `q`, `status`, `role`, `include_deleted`; server-paged with `limit`/`offset`; case-insensitive substring search across email + name fields), `GET /users/{id}`, `PATCH /users/{id}` (first_name/last_name/display_name/email/role; email or role change **bumps `ver`** so live access tokens die and revokes all sessions with `revoked_reason='admin_update'`; email change clears `email_verified_at`; email dedupe check against other active users returns 409), `POST /users/{id}/suspend` (status=SUSPENDED, `suspended_at`+`suspended_reason` set, ver++, sessions revoked with `revoked_reason='suspended'`), `POST /users/{id}/reactivate` (status=ACTIVE, clears suspend fields; rejects reactivating a soft-deleted user with 409 — restore flow deferred per FR-USR-08), `POST /users/{id}/force-reset` (creates a 30-min `password_resets` row, sets `must_change_password=true`, ver++, revokes sessions with `revoked_reason='force_reset'`, mails the reset link via existing `password_reset_email` template — same email template used by user-initiated `/password/forgot`), `DELETE /users/{id}` (soft: status=SOFT_DELETED, `deleted_at`+`deleted_by` set, ver++, sessions revoked), `DELETE /users/{id}?hard=true` (row removed). **Self-protection:** admin cannot suspend or delete themselves — returns 409. Permissions map to existing keys: `user:read`, `user:write`, `user:suspend`, `user:force_reset`, `user:delete` (all in `_ADMIN`). Audit actions emitted per mutation: `USER_UPDATED`, `USER_SUSPENDED` (SEC/denied), `USER_REACTIVATED`, `USER_FORCE_RESET` (SEC), `USER_SOFT_DELETED` (SEC), `USER_HARD_DELETED` (SEC). ponytail: raw SQL inline against `users`, no service layer — surface is small enough. `scripts/admin_users_smoke.py` (invites a throwaway user then exercises list/get/patch/suspend/reactivate/force-reset/self-protection/soft-delete/hard-delete, checks all 6 audit actions land). |
| 31 | `correlation-ids-and-structured-logs` | `auth/observability.py` — `RequestIdMiddleware` reads `X-Request-ID` (or mints a uuid4-hex, capped at 64 chars), stashes it on `request.state.request_id` and a `ContextVar`, echoes it back on the response, and emits one JSON access-log line per request (`{ts, level, logger, event, request_id, method, path, status, dur_ms, user_id}`). `_JsonFormatter` also pulls the ContextVar for any log line that didn't set `request_id` explicitly. `configure_logging()` installs the formatter on the root logger at lifespan startup (`LOG_LEVEL` env, default `INFO`); uvicorn / httpx / cfv.* loggers propagate through it, so every line is JSON. `auth/middleware.py::_with_request_id` and `auth/routes.py::_request_id` now prefer `request.state.request_id` (middleware-set) over the raw header. Audit rows persisted from a request already flow through `AuditEvent(..., request_id=...)` — no service change needed. `scripts/request_id_smoke.py` (4 checks: client header echoed, auto-generated 32-hex when absent, audit row carries the id, access-log JSON line contains it). |
| 30 | `rate-limit-and-lockout` | `auth/rate_limit.py` — `check_and_consume(category, key, limit, window_seconds)` sliding-window (4 sub-buckets) over the existing `rate_limit_buckets` table; runs in its own committed tx so counters survive the login-failure rollback that would otherwise revert them. Emits `SEC_RATE_LIMITED` audit on breach with `{category, key, limit, window_seconds}` context. `/api/v1/auth/login` enforces IP 5/15m + email 10/15m (raise 429 with `Retry-After`); `/api/v1/auth/password/forgot` enforces email 3/1h + IP 20/1h (**silently drops the reset work but still returns 202** per spec §B.11). Lockout: `AuthService.login` bad-password branch now bumps `users.failed_login_attempts` in a separate committed tx (`_commit_failed_attempt`); on threshold (default 5) it sets `locked_until = now() + 15 min`, zeros the counter, emits `SEC_LOCKOUT_STARTED`, and the caller raises `AccountLocked` → 423. Config knobs added to `AuthSettings`: `AUTH_LOCKOUT_THRESHOLD`, `AUTH_LOCKOUT_WINDOW_MIN`, `AUTH_RL_{LOGIN_IP,LOGIN_EMAIL,FORGOT_EMAIL,FORGOT_IP}_{LIMIT,WINDOW}`. `errors.py::_problem` now forwards `HTTPException.headers` so `Retry-After` survives the problem+json reshape. `scripts/rate_limit_smoke.py` (4 checks: login 429 + Retry-After, forgot stays 202 when throttled, lockout persists in DB, both `SEC_RATE_LIMITED` + `SEC_LOCKOUT_STARTED` audits emitted). |
| 29 | `audit-logger-hash-chained` | `auth/audit.py` — real `AuditLogger` with SHA-256 hash chain, `pg_advisory_xact_lock` serialization, genesis = 64 hex zeros. `emit(conn=None, ctx, event, ...)` — **passing None (the default) opens its own committed tx**, so audits survive when the caller's request tx rolls back on any raised exception; passing an existing conn is for the atomic-multi-write case (only `_commit_reuse_security`). `verify_chain()` walks in insertion order, recomputes each row's hash, returns `{ok, checked, first_bad, reason}`. Service methods emit `AUTH_LOGIN_SUCCESS/FAILURE`, `AUTH_TOKEN_REFRESH{,_FAILURE}`, `AUTH_REFRESH_REUSE_DETECTED` (SEC), `AUTH_LOGOUT{,_ALL}`, `USER_ACCEPTED_INVITE`, `AUTH_EMAIL_VERIFY_SUCCESS`, `AUTH_PASSWORD_RESET_REQUESTED`, `AUTH_PASSWORD_RESET` (SEC), `AUTH_PASSWORD_CHANGED` (SEC), `AUTH_PASSWORD_CHANGE_FAILURE`, `USER_INVITED`; permissions `require()` emits `PERMISSION_DENIED` (denied outcome) in its own tx before raising 403. Every service method now takes `request_id`+`ip_address`+`user_agent`; routes plumb them. `scripts/verify_audit_chain.py` + `scripts/audit_smoke.py` (verified 5 expected actions land, chain intact across all rows). |

### Bonus / off-plan fixes that happened during M1

- **Timezone correctness** — `datetime.fromisoformat(datetime_obj)` crash in `scheduler._due_now` after the Postgres port; fixed to accept aware datetimes and normalize to `LOCAL_TZ`. `compute_next_run` now emits ISO with offset (`-04:00`/`-05:00`).
- **`fmtRelative`** — `Math.floor` → `Math.round`, dropped the "+ 'Z'" hack that would have UTC-tagged legacy strings.
- **`?` → `%s`** was missed on `/api/pdfs/{id}/content`; found + fixed after the pdf viewer 500'd.
- **Content-dedupe** — new `text_hash` column on `pdfs` (SHA-256 of normalized extracted PDF text). `scan.py` computes it; `commit.py` dedupes on `WHERE md5 = %s OR text_hash = %s`. `scripts/backfill_text_hash.py` populated all 101 existing rows and found 3 clusters (9 extras) that had passed byte-dedupe because vendor embeds fresh `/CreationDate` per download. `scripts/prune_content_dupes.py --confirm` deleted the 8 extras + kept the oldest-committed row per cluster.
- **File explorer & Files-page filename column** — for committed rows, display uses `basename(dest_path)` (the clean on-share name) with the historical source filename in a hover tooltip. Search bag matches the displayed name.
- **Hydration warning** — added `suppressHydrationWarning` on `<html>` for the Sigcapture browser extension attribute injection.
- **Files page** — client-side pagination (25/50/100), backend `/api/pdfs` LIMIT dropped.
- **Command palette** — Ctrl+K global fuzzy over already-loaded PDFs + PCs; jumps to `/files?open=<id>` / `/pcs?open=<pc>`.
- **Search & filters Phase 11 (broader plan)** — command palette + Export view CSV shipped; multi-select filter panel / saved presets / FTS5 explicitly deferred.

### Milestone status

| Milestone | Status |
|---|---|
| **M1 — auth spine** | ✅ shipped (branches 22–28) |
| **M2 — ops-safe basics** | ✅ shipped (branches 29–33). |
| M3 — MFA + password hygiene | ⬜ not started (TOTP, backup codes, HIBP, history, zxcvbn) |
| M4 — devices + admin dashboard + risk-lite | ⬜ not started |
| M5 — API keys | ⬜ not started |
| M6+ | Backlog (SMS, ABAC, GraphQL, teams, WebAuthn, ML risk, etc.) |

### Alembic revision graph (as of now)

```
89986958c76a  initial auth schema (users, sessions, mfa_*, audit_events, api_keys, mail_outbox, ...)
     ↓
6c342edcdb59  app tables from sqlite (pdfs, scan_runs, pc_status, schedule)
     ↓
56bd941a6dbd  pdfs.text_hash for content dedupe   ← current head
```

### Files on disk (auth surface, for quick orientation)

```
auth/
  __init__.py     audit.py         context.py       deps.py
  emails.py       engine.py        errors.py        http_security.py
  middleware.py   passwords.py     permissions.py   random.py
  routes.py       service.py       services.py       ← compat re-export shim
  sessions.py     settings.py      token_state.py   tokens.py
scripts/
  audit_smoke.py           auth_smoke.py         backfill_text_hash.py
  create_admin.py          create_db.py          db_ping.py
  endpoints_smoke.py       gen_jwt_key.py        invite_flow_smoke.py
  invite_user.py           migrate_from_sqlite.py prune_content_dupes.py
  rbac_smoke.py            token_smoke.py        verify_audit_chain.py
backend/alembic/versions/
  89986958c76a_initial_auth_schema.py
  6c342edcdb59_app_tables_from_sqlite.py
  56bd941a6dbd_pdfs_text_hash_for_content_dedupe.py
```

### How to pick this back up next session

1. Sanity check the whole surface: `uv run --env-file .env python scripts/rbac_smoke.py --email admin@aw.local --password 'Correct-Horse-Battery-9!'` (18 checks), then `endpoints_smoke.py` (9), `invite_flow_smoke.py` (11), `audit_smoke.py` (5 actions + chain verify).
2. Bootstrap admin creds (unchanged since branch 24): email `admin@aw.local`, password `Correct-Horse-Battery-9!`. Recreate with `scripts/create_admin.py` if lost.
3. Restart both processes: backend `uv run --env-file .env uvicorn api:app --reload --host 0.0.0.0 --port 8000`; frontend `cd frontend && pnpm dev`.
4. Next milestone: **M3 — MFA + password hygiene** (TOTP, backup codes, HIBP, history, zxcvbn).

---

## 0 · Scope Reality Check (read this first)

You asked for the enterprise platform. It's all designed below. Before you approve building all of it, calibrate against the real deployment.

**Current deployment:** LAN-only internal tool, 24 lab PCs, 1–3 admins, single server, SQLite, no external users, no compliance regime named. There is currently zero auth on the app at all.

**Full-spec vs. LAN reality:**

| Requested feature | Real ROI at LAN scale | Recommended posture |
|---|---|---|
| Password login + Argon2id + session cookies | High — this is the whole point | **Build M1** |
| Admin-created accounts (no self-registration) | High — matches your model | **Build M1** |
| RBAC (admin/operator/viewer) | High — you already sketched these three roles | **Build M1** |
| Audit log (auth events + role changes + edits) | High — this is what makes the platform trustworthy | **Build M2** |
| Rate limiting + brute-force lockout | High — cheap and correct | **Build M2** |
| Password reset via email (reuses your SMTP) | High — matches PLAN.md line 158 | **Build M2** |
| TOTP MFA (authenticator app) | Medium — good hardening for admin | **Build M3** |
| Session/device management UI | Medium — pairs with MFA nicely | **Build M3** |
| Password history / expiration / complexity | Medium — pick 2 of 3, not all | **Build M3, pick sparingly** |
| CSRF / CORS / secure cookies / CSP headers | High — free wins | **Build M1 (bundled)** |
| Full ABAC policy engine (attributes + expressions) | **Low** — your entities are files, PCs, users; RBAC covers 100% of your access decisions today | **Design only, defer M6+** |
| SMS OTP | **Low** — needs Twilio, phone numbers, cost per message, all for LAN admins | **Design only, likely skip forever** |
| Push notifications | **Low** — no mobile app | **Skip** |
| Slack / webhook notifiers | **Low** — email already covers you | **Design only, defer** |
| Password breach detection (HIBP k-anon) | Medium — one HTTP call per set-password, easy win | **Build M3** |
| Risk scoring / anomaly detection / credential stuffing | **Low** — you have 3 users on a LAN; there is no adversarial credential-stuffing traffic to detect | **Design only, defer indefinitely** |
| Suspicious-login detection | Medium — cheap heuristic (new IP + new UA) is plenty | **Build M4 (cheap version only)** |
| Backup codes | High — pairs with MFA, prevents lockout | **Build M3** |
| GraphQL compatibility | **Low** — REST is fine, and you have no GraphQL clients | **Design only, likely skip forever** |
| OpenAPI/Swagger | Free with FastAPI — already generated | **Build M1 (turn on)** |
| RFC 9457 Problem Details errors | High — you can normalize error shape once | **Build M1** |
| Idempotency keys | **Low** — you don't have replayed writes in this tool | **Design only, defer** |
| Request tracing / correlation IDs | Medium — helpful debugging, one middleware | **Build M2** |
| Feature flags | **Low** — YAGNI at 1–3 admins | **Skip** |
| Teams | **Low** — you don't have multi-team structure | **Design only, defer** |
| Invitations flow | Medium — nicer than "admin sets a temp password" | **Build M4** |
| Merge accounts | **Low** — 3-user pool, no duplicates | **Skip** |
| Email verification on account create | High — table stakes | **Build M1** |
| Key rotation / secrets management | Medium — env file is fine; add rotation later | **M6+** |
| Compliance logging | **Zero** without a named compliance regime | **Skip until named** |
| API keys | Medium — useful for the scanner/CLI to hit the API | **Build M5** |
| Admin dashboard | High — you need it for user/role management | **Build M4** |
| Security dashboard | Medium — small tile on Admin, not its own page | **Build M4 (mini)** |

**TL;DR of the reality check:** build **M1–M5** as designed. **M6+** is designed on paper but should only be built when a named driver appears (compliance audit, second organization, adversarial traffic). Ship-first-then-question is the ponytail rule. Full design continues below.

---

# PART A · Software Requirements Specification (SRS)

## A.1 · Purpose

Introduce authentication, authorization, and identity management to Client Files Viewer v2 in a way that:

1. Is **safe by default** — hardened against the OWASP ASVS L2 attacks common to internal tools.
2. Is **operable by one admin** — no infra explosion; runs on the same FastAPI process, same SQLite database (with a documented Postgres migration path).
3. Is **extensible** — RBAC surfaces are pluggable so ABAC and per-resource policy can layer in without a rewrite.
4. Preserves the existing UX — logged-in single-page flow, no per-navigation redirect storms.

## A.2 · Actors & roles

| Actor | Description | Default permissions |
|---|---|---|
| **Anonymous** | Unauthenticated request | `/api/health` + login endpoints only |
| **Viewer** | Read-only user | GET on `pdfs`, `pcs`, `logs`, `runs`, `pdf content` |
| **Operator** | Day-to-day scan/commit operator | Viewer + POST `scans`, `commits`, `bulk`, PATCH `pdfs/*` (client name edit) |
| **Admin** | Everything | Operator + user/role/settings management, DELETE, force-rescan, config |
| **Service** | Non-human API caller (scheduler, CLI, integrations) | Scoped API key — see FR-AZ-06 |

Roles are seeded at first-run migration; custom roles land in M6.

## A.3 · Functional Requirements

Numbered `FR-<AREA>-<n>`. All requirements are **testable**: acceptance criteria must be observable in an integration test or a real request/response.

### A.3.1 · Authentication (FR-AUTH-*)

| ID | Requirement |
|---|---|
| FR-AUTH-01 | Users authenticate with email + password. Case-insensitive email lookup with unique normalized form. |
| FR-AUTH-02 | Successful authentication issues an **access token** (JWT, 15-minute TTL) and a **refresh token** (opaque, 30-day TTL) bound to a session row. |
| FR-AUTH-03 | Refresh endpoint rotates the refresh token (new opaque value) and issues a new access token. Old refresh token is invalidated. |
| FR-AUTH-04 | Refresh token reuse (a token already rotated) revokes the entire session family and forces re-login. Reuse is logged as `SECURITY_REFRESH_REUSE`. |
| FR-AUTH-05 | Logout revokes the session's refresh token immediately. Access token stays valid until expiry (documented; acceptable at 15-min TTL). |
| FR-AUTH-06 | Logout-all endpoint revokes every session for the user. |
| FR-AUTH-07 | Access token is a signed JWT with claims: `sub`, `iat`, `exp`, `sid` (session id), `roles[]`, `ver` (user version — bumped on role change or force-logout). |
| FR-AUTH-08 | JWT verification rejects tokens with `ver` mismatch, revoked `sid`, expired `exp`, or wrong `iss/aud`. |
| FR-AUTH-09 | **Only admins** can create accounts. No public self-registration endpoint exists. |
| FR-AUTH-10 | New accounts must verify email before first login. Verification link is single-use, 24-hour TTL. |
| FR-AUTH-11 | "Remember me" extends refresh token TTL to 90 days on that session only (default: 30 days when unchecked). |
| FR-AUTH-12 | Login accepts either browser (cookie) or API client (JSON w/ Bearer) mode. Both share the same rate-limit + audit path. |
| FR-AUTH-13 | Cookies are set as `HttpOnly`, `Secure`, `SameSite=Lax`, `__Host-` prefix, `Path=/`. |
| FR-AUTH-14 | The frontend never sees or stores the refresh token; only the access token, in memory (not localStorage). |
| FR-AUTH-15 | API-key authentication is supported for service accounts — see FR-AZ-06 for scope model. |
| FR-AUTH-16 | Password login response never varies in status code or timing between "no such user", "wrong password", and "unverified user"; it varies only in generic 401 + rate-limit counter. |

### A.3.2 · Password Management (FR-PWD-*)

| ID | Requirement |
|---|---|
| FR-PWD-01 | Passwords are hashed with **Argon2id** (`time_cost=3, memory=64MiB, parallelism=4`). Never stored or logged in plaintext. |
| FR-PWD-02 | Password complexity: min 12 chars, must contain 3 of {upper, lower, digit, symbol}. Zxcvbn score ≥ 3 required. |
| FR-PWD-03 | Forgot-password: any request returns 202 with the same generic message. Token emailed to registered email only, 30-min TTL, single-use, invalidated on password change. |
| FR-PWD-04 | Reset-password endpoint accepts the token + new password, invalidates all sessions of the user, and sends a `PASSWORD_CHANGED` alert email. |
| FR-PWD-05 | Change-password endpoint requires current password + new password. Applies same complexity + history rules. |
| FR-PWD-06 | Password history: last **12** password hashes retained. Reuse blocked (Argon2 verify against each; comparisons capped by time budget). |
| FR-PWD-07 | Password expiration: default **off**. If enabled by admin, password age forces reset at N days (configurable). Warning banner shown 7 days before expiry. |
| FR-PWD-08 | Password breach detection: on set-password, k-anonymity check against HaveIBeenPwned range API (SHA-1 prefix of 5). Failure blocks with clear message. Network failure logs warning but does not block (fail-open with audit). |
| FR-PWD-09 | Admin can force-reset any user's password. Target session is revoked, email sent. |
| FR-PWD-10 | Password fields in all API responses are omitted. Password fields in audit records are stored as `[REDACTED]`. |

### A.3.3 · Multi-Factor Authentication (FR-MFA-*)

| ID | Requirement |
|---|---|
| FR-MFA-01 | MFA factor types supported: **TOTP** (RFC 6238), **email OTP**, **SMS OTP** (behind feature flag), **backup codes**. |
| FR-MFA-02 | MFA is **optional per-user** by default. Admins can mark MFA as **required** globally, or per-role. Marking as required forces enrollment on next login. |
| FR-MFA-03 | TOTP enrollment: server generates a random 20-byte secret, encodes as `otpauth://` URI + QR, verifies one code before persisting the factor. |
| FR-MFA-04 | Email OTP: 6-digit code, 10-min TTL, delivered via existing SMTP. Rate-limited to 3 codes / 15 min / user. |
| FR-MFA-05 | SMS OTP: 6-digit code, 10-min TTL, delivered via pluggable adapter (Twilio or stub). Rate-limited. Feature-flagged. |
| FR-MFA-06 | Backup codes: 10 codes, single-use, generated on MFA enrollment and on request. Hashed at rest (Argon2). |
| FR-MFA-07 | Login flow is **two-step** when MFA is enabled: password OK → server issues a short-lived (5 min) `mfa_challenge` token → user submits factor code + challenge → tokens issued. |
| FR-MFA-08 | Wrong MFA codes count toward brute-force limits; 5 failures within 15 minutes revokes the challenge and forces re-login. |
| FR-MFA-09 | Admin can revoke a user's MFA factors (support scenario: lost device). Revocation is audited. |
| FR-MFA-10 | If a user has multiple factors, they may choose which to use; the last-used is remembered. |

### A.3.4 · Authorization (FR-AZ-*)

| ID | Requirement |
|---|---|
| FR-AZ-01 | RBAC model: `users -N:N-> roles -N:N-> permissions`. Permissions are `resource:action` strings (e.g. `pdf:read`, `pdf:delete`, `user:manage`). |
| FR-AZ-02 | Seed roles at first run: **admin**, **operator**, **viewer**. Their permissions match the table in A.2. |
| FR-AZ-03 | Custom roles: admins may create/delete non-seed roles and assign permissions to them. Seed roles cannot be deleted or renamed. |
| FR-AZ-04 | Permission hierarchy: a permission may `implies[]` other permissions (e.g. `pdf:delete` implies `pdf:read`). Resolution is transitive with a cycle-guard. |
| FR-AZ-05 | Every protected endpoint declares the exact permission required. Middleware evaluates at request time; denials return RFC 9457 `403 Forbidden` with `type=/errors/forbidden`. |
| FR-AZ-06 | API keys carry **scopes** (subset of permissions). Scopes are checked identically to roles at endpoint level. |
| FR-AZ-07 | Role changes bump the user's `ver`, invalidating all outstanding access tokens on next refresh check. |
| FR-AZ-08 | ABAC (design only for M1–M5): a policy DSL accepts `subject.*`, `resource.*`, `action`, `context.*` attributes and returns `permit / deny / not-applicable`. Evaluated **after** RBAC allow, never in place of it. |
| FR-AZ-09 | Authorization failures are audited with subject, resource id, permission required, decision. |
| FR-AZ-10 | Every endpoint has a default of **deny**. Un-annotated endpoints fail closed. |

### A.3.5 · User & Team Management (FR-USR-*)

| ID | Requirement |
|---|---|
| FR-USR-01 | User profile fields: `id`, `email`, `display_name`, `avatar_url`, `time_zone`, `locale`, `status`, `mfa_required`, `created_at`, `last_login_at`. |
| FR-USR-02 | Statuses: `INVITED`, `ACTIVE`, `SUSPENDED`, `DEACTIVATED`, `SOFT_DELETED`. State machine strictly enforced. |
| FR-USR-03 | **Invitation flow**: admin issues invite → invitee receives email w/ single-use link → sets password → account moves to `ACTIVE`. |
| FR-USR-04 | Deactivation: user cannot log in, sessions revoked, data retained. Reversible. |
| FR-USR-05 | Suspension: same effect as deactivation but marked as a security action. Includes a `reason` field, admin-only. |
| FR-USR-06 | Soft delete: user row marked `SOFT_DELETED`, PII (email, name) tombstoned, foreign-key references preserved. Reversible within retention window (default 30 days). |
| FR-USR-07 | Hard delete: purge PII completely and rewrite foreign-key references to a sentinel "deleted user" row. Irreversible. Admin-only, requires confirmation. |
| FR-USR-08 | Account recovery: admin can reactivate a soft-deleted user within retention window. |
| FR-USR-09 | Teams (design only): a team groups users. Roles may be assigned at (user, team) pair. **Not built in M1–M5.** |
| FR-USR-10 | Account merge (design only, not built): admin picks primary + secondary; all owned records reparent to primary; secondary hard-deleted. Requires audit event `USER_MERGED` with both ids. |

### A.3.6 · Security (FR-SEC-*)

| ID | Requirement |
|---|---|
| FR-SEC-01 | Rate limits by category: `login` (5/15min/IP + 10/15min/email), `mfa` (5/15min/user), `password_reset_request` (3/hour/email + 20/hour/IP), `api` (100/min/user), `admin_write` (60/min/user). |
| FR-SEC-02 | Brute-force lockout: 5 failed logins in 15 min → account locked for 15 min. Escalates: 3rd lockout in 24h → 24h lockout + admin alert. |
| FR-SEC-03 | Suspicious-login detection (cheap): a login from a new (IP prefix, user-agent hash) pair triggers a `SECURITY_ALERT` email to the user with device + IP + time. |
| FR-SEC-04 | CSRF: cookie-mode sessions require a double-submit CSRF token on unsafe methods. Bearer-mode requests are exempt. |
| FR-SEC-05 | CORS: allow-list origins from env (`ALLOWED_ORIGINS`). No wildcard for authenticated endpoints. |
| FR-SEC-06 | XSS: all API responses are `application/json` with `X-Content-Type-Options: nosniff`. CSP header set on frontend pages: `default-src 'self'; script-src 'self'; object-src 'none'; frame-ancestors 'none'`. |
| FR-SEC-07 | SQL injection prevention: all queries use parameterized statements. No string interpolation on user input into SQL. Enforced via CI grep for f-string SQL. |
| FR-SEC-08 | Timing-attack prevention: password + token comparisons use constant-time (`hmac.compare_digest` or Argon2 verify). Login response variance target ≤ 20ms. |
| FR-SEC-09 | Secure cookies (see FR-AUTH-13). Session cookies rotate value on privilege elevation (login → MFA-pass). |
| FR-SEC-10 | CSP header, HSTS (`max-age=31536000; includeSubDomains`), `Referrer-Policy: strict-origin-when-cross-origin`, `X-Frame-Options: DENY`. |
| FR-SEC-11 | Key rotation: signing keys have a **kid**. New kid published; old kid still verifies for the length of one refresh window; then retired. Automated procedure documented. |
| FR-SEC-12 | Secrets management: all secrets read from environment variables at startup. `.env` never committed. Any log line that would emit an env var is filtered. |
| FR-SEC-13 | Replay-attack prevention (JWT): `jti` claim; server maintains a small revocation set for logouts within the access-token TTL. |
| FR-SEC-14 | Idempotency keys (design only): mutating endpoints may accept `Idempotency-Key` header; server dedupes within a 24-hour window. **Not built in M1–M5.** |

### A.3.7 · API (FR-API-*)

| ID | Requirement |
|---|---|
| FR-API-01 | REST first. All endpoints under `/api/v1/…`. |
| FR-API-02 | Versioning is URI-based; v1 → v2 will run side-by-side during transition. |
| FR-API-03 | Pagination: `?page=1&size=25` (defaults 1/25, max size 200). List responses include `{ items, page, size, total }`. |
| FR-API-04 | Filtering: RSQL-lite grammar `?filter=field:op:value,...` (ops: `eq`, `neq`, `lt`, `lte`, `gt`, `gte`, `like`, `in`). Whitelisted fields per resource. |
| FR-API-05 | Sorting: `?sort=field,-field2` (prefix `-` = desc). Whitelisted fields per resource. |
| FR-API-06 | OpenAPI 3.1 spec generated by FastAPI, exposed at `/api/openapi.json` and Swagger UI at `/api/docs` (admin-only in prod). |
| FR-API-07 | Error responses use **RFC 9457 Problem Details**: `{ type, title, status, detail, instance, code, errors? }`. Same shape everywhere. Never leaks stack traces. |
| FR-API-08 | Every request has a `X-Request-ID` (server generates if absent). ID is echoed on responses and included in logs + audit rows. |
| FR-API-09 | GraphQL: design only. If added later, uses the same auth middleware and permission model at the resolver level. |
| FR-API-10 | All timestamps are ISO-8601 UTC; all sizes are bytes. |

### A.3.8 · Email (FR-EML-*)

| ID | Requirement |
|---|---|
| FR-EML-01 | Email templates (plain-text; HTML not needed at LAN scale): `INVITE`, `EMAIL_VERIFY`, `PASSWORD_RESET`, `PASSWORD_CHANGED`, `EMAIL_CHANGED`, `SECURITY_ALERT`, `MFA_ENROLLED`. |
| FR-EML-02 | Emails send through existing SMTP config (Phase 7). Failures are logged as warnings, retried once, then surfaced in admin dashboard. |
| FR-EML-03 | Sensitive-action emails (password change, email change, MFA enroll) always send even if the triggering action was initiated by the user. Prevents silent account takeover. |
| FR-EML-04 | Emails are sent via an async queue (SQLite-backed) so the request thread doesn't wait on SMTP. |
| FR-EML-05 | Emails never contain the plaintext password, token secret, or backup codes. Verification/reset links contain a hashed token opaque to the mail path. |

### A.3.9 · Notifications (FR-NOT-*)

| ID | Requirement |
|---|---|
| FR-NOT-01 | Notification channels supported: **email** (required), **webhook** (M6), **Slack** (M6), **push** (deferred). |
| FR-NOT-02 | Notification routing rule: `(event_type, user_or_role) → channel[]`. Configurable via admin. |
| FR-NOT-03 | Webhook delivery uses HMAC-SHA256 signature with a per-endpoint secret and a `X-CFV-Timestamp` for replay protection. Retries 3× with exponential backoff. |
| FR-NOT-04 | Slack integration is a bot token in env; posts to a channel per rule. |

### A.3.10 · Audit Logging (FR-AUD-*)

| ID | Requirement |
|---|---|
| FR-AUD-01 | Every auth event, security event, role/permission change, admin action, and self-service profile change writes a row to `audit_events`. |
| FR-AUD-02 | Row fields: `id`, `at`, `actor_id` (nullable), `actor_type` (user/service/anonymous/system), `action`, `target_type`, `target_id`, `ip`, `user_agent`, `request_id`, `context_json`, `outcome` (`success`/`failure`/`denied`), `prev_hash`, `hash`. |
| FR-AUD-03 | Rows are append-only. `hash` is `SHA-256(prev_hash || canonical_json(row_sans_hashes))`. Any tamper breaks the chain and is flagged by a nightly verifier. |
| FR-AUD-04 | Retention default: **1 year** for `INFO` events, **7 years** for security events (configurable). Rotated older rows are exported to a compressed archive on the destination share. |
| FR-AUD-05 | Audit rows are queryable by admins via API + admin UI with filters: actor, action, target, date range, outcome. |
| FR-AUD-06 | Redaction: audit rows never contain plaintext passwords, tokens, or MFA secrets. |
| FR-AUD-07 | Failure audit: any denied permission check writes a row with `outcome=denied` and the permission that would have been required. |

### A.3.11 · Administration (FR-ADM-*)

| ID | Requirement |
|---|---|
| FR-ADM-01 | Admin dashboard is a page in the frontend behind the `admin:*` permission set. |
| FR-ADM-02 | User search: paginated table with filters (status, role, MFA enrolled?, last login ≥ N days ago), free-text on email/display name. |
| FR-ADM-03 | Role management: create/edit/delete custom roles, assign/revoke permissions. Cannot demote self out of admin (guard). |
| FR-ADM-04 | Permission management: read-only browse of all permissions grouped by resource. |
| FR-ADM-05 | System settings: MFA global policy, password expiration policy, session TTLs, email templates preview, feature flags. |
| FR-ADM-06 | Security dashboard tile: last 20 security events, active lockouts, failed-login rate chart. |
| FR-ADM-07 | Audit viewer: search + filter over `audit_events`, CSV export. |
| FR-ADM-08 | Feature flags (design only): boolean flags stored in DB, changeable at runtime, evaluated per-user optionally. **Not built in M1–M5.** |

## A.4 · Non-Functional Requirements

### A.4.1 · Performance

| ID | Requirement |
|---|---|
| NFR-PERF-01 | Login p95 ≤ 300ms (Argon2 verify dominates). Login p99 ≤ 500ms. |
| NFR-PERF-02 | Access-token verification p99 ≤ 5ms (no DB hit; JWKS in memory + `ver` lookup cache). |
| NFR-PERF-03 | Permission check p99 ≤ 10ms (permission set cached per token). |
| NFR-PERF-04 | Audit write must not block request response; queued and flushed by a background writer. |

### A.4.2 · Availability

| ID | Requirement |
|---|---|
| NFR-AV-01 | Auth service is same process as the app. Availability = app availability. |
| NFR-AV-02 | Email delivery failures do not block auth flows (queued, retried; audit path always writes even if email fails). |

### A.4.3 · Security

| ID | Requirement |
|---|---|
| NFR-SEC-01 | Meets OWASP ASVS Level 2 for the built subset. |
| NFR-SEC-02 | All secrets are pulled from env; grep of the repo for known secrets returns nothing. |
| NFR-SEC-03 | Dependency vulnerabilities: `pip-audit` in CI fails the build on `HIGH`/`CRITICAL`. |
| NFR-SEC-04 | HSTS, CSP, and cookie flags verified in an integration test against the running app. |

### A.4.4 · Observability

| ID | Requirement |
|---|---|
| NFR-OBS-01 | Structured JSON logs. Every log line has `request_id`, `user_id` (when known), `event`. |
| NFR-OBS-02 | Prometheus-style `/metrics` endpoint (admin-only): counters for logins/day, failed logins, lockouts, MFA challenges, audit rows/hour. |
| NFR-OBS-03 | Correlation ID passes through every service call and lands in every audit + log row. |

### A.4.5 · Compliance

| ID | Requirement |
|---|---|
| NFR-COMP-01 | Design does not preclude SOC2 / HIPAA. No compliance regime is claimed until named by leadership. Audit + retention + encryption at rest are prerequisites already covered. |

### A.4.6 · Portability

| ID | Requirement |
|---|---|
| NFR-PORT-01 | Postgres is the DB (existing `clientfiles` at 192.168.70.10). Schema authority is Prisma. Python side speaks Postgres directly via `asyncpg`; no cross-DB portability required. |

## A.5 · Constraints & Assumptions

- Existing stack: FastAPI (Python 3.12), SQLite, Next.js. Do not switch stacks for this feature.
- Existing SMTP (Phase 7) is reused for all mail.
- LAN-only deployment initially. TLS provided by a reverse proxy (nginx / Caddy) in front of the app.
- Server clock is NTP-synced (needed for TOTP window).
- No external identity provider (no OAuth / SAML) in M1–M5. Add as adapters when a customer asks.

## A.6 · Out of Scope (permanently, unless re-scoped)

- Social login (Google, GitHub) — no user demand
- SAML / SCIM — no enterprise customer to integrate with
- Passkeys / WebAuthn — considered for M6 as a TOTP alternative; not required in first releases
- Biometric authentication
- Federated identity across tenants (no multi-tenancy in this app)
- Multi-region replication of the auth store

---

# PART B · Technical Design Document (TDD)

## B.0 · Existing Schema Baseline (Postgres + Prisma)

The Prisma schema at `D:\Code\aw\client-files-viewer\backend\prisma\schema.prisma` (owned by the v1 Node app) already models most of the auth surface. **Reuse what's there, add what's missing, don't rebuild.**

### B.0.1 Reused as-is

| Existing model | Covers |
|---|---|
| `User` | `id`, `email`, `password` (Argon2 hash), `firstName`, `lastName`, `role` (enum), `isActive`, `isEmailVerified`, `mustChangePassword`, `avatarStyle/Seed`, `emailVerificationToken/Expiry`, `passwordResetToken/Expiry`, `lastLoginAt/Ip`, `failedLoginAttempts`, `lockedUntil`, `createdAt/updatedAt` |
| `Session` | `id`, `userId`, `token` (unique), `expiresAt`, `createdAt` — **augmented**, see B.0.3 |
| `AuditLog` | `id`, `userId`, `action`, `resource`, `details` (JSON), `ipAddress`, `createdAt` — **augmented**, see B.0.3 |
| `LoginHistory` | `id`, `userId`, `ipAddress`, `userAgent`, `device`, `location`, `success`, `failReason`, `createdAt` |
| `PasswordHistory` | `id`, `userId`, `password` (hash), `createdAt` |
| `Device` | `id`, `userId`, `fingerprint`, `deviceName`, `deviceType`, `browser`, `browserVersion`, `os`, `osVersion`, `ipAddress`, `isTrusted`, `firstSeenAt`, `lastSeenAt` |

### B.0.2 Existing `Role` enum

```prisma
enum Role { SUPER_ADMIN  ADMIN  MANAGER  USER }
```

**Design decision:** keep this enum as the **primary role assignment** through M1–M5. Map it to the RBAC permission model as a fixed table:

| Enum value | Permission set |
|---|---|
| `SUPER_ADMIN` | Everything, including role/permission management |
| `ADMIN` | User management + all data actions (no permission table edits) |
| `MANAGER` | Operator (scan, commit, edit client names, view all) |
| `USER` | Viewer (read-only) |

**Custom roles + normalized `roles/permissions/user_roles/permission_implies` tables are deferred to M6** (originally M1 in v1 of this doc). This is a ponytail win — the enum covers 100% of foreseeable role assignments at this deployment, and the schema-add cost stays low.

### B.0.3 Fields/tables to ADD via Prisma migration

Diff-oriented — one migration PR per group.

**Extend `User`:**
- `emailNormalized  String  @unique` — lowercased for lookup; keep `email` as display-cased
- `ver              Int     @default(1)` — bumped on role change / force-logout to invalidate access tokens
- `mfaRequired      Boolean @default(false)` — user-level MFA gate
- `mfaEnrolledAt    DateTime?` — first successful MFA verify
- `deletedAt        DateTime?` — soft-delete tombstone
- `deletedBy        String?` — actor id
- `timeZone         String  @default("America/New_York")`
- `locale           String  @default("en-US")`
- `passwordUpdatedAt DateTime?` — password-age policy
- `suspendedAt      DateTime?` — separate from `isActive` (deactivate vs security-suspend)
- `suspendedReason  String?`

**Extend `Session`** (major — this is what makes refresh-family revocation work):
- Rename `token` → `refreshTokenHash` (Argon2 hash of opaque token; index unique)
- `parentId       String?` — refresh-family lineage (FK self)
- `remembersMe    Boolean @default(false)`
- `userAgent      String`
- `ipAddress      String`
- `lastUsedAt     DateTime`
- `revokedAt      DateTime?`
- `revokedReason  String?`
- `deviceId       String?` — link to `Device`

**Extend `AuditLog`** for hash-chain + richer context:
- `actorType    String  @default("user")` — user/service/anonymous/system
- `targetType   String?`
- `targetId     String?`
- `userAgent    String?`
- `requestId    String` — correlation id
- `outcome      String  @default("success")` — success/failure/denied
- `severity     String  @default("INFO")` — INFO/WARN/SEC
- `prevHash     String`
- `hash         String  @unique`
- `at           DateTime @default(now())` (already `createdAt`; either rename in a follow-up or leave as-is and treat `createdAt` as the timestamp)
- `userId       String?` — **make nullable** so anonymous/system events are recordable
- Index `(createdAt DESC)`, `(action, createdAt)`, `(severity, createdAt)`

**New tables:**

```prisma
// --- MFA ---
model MfaFactor {
  id                String    @id @default(uuid())
  userId            String
  kind              MfaKind
  label             String?
  secretEncrypted   Bytes?    // CryptoBox-sealed TOTP seed / phone / email
  createdAt         DateTime  @default(now())
  lastUsedAt        DateTime?
  revokedAt         DateTime?
  user              User      @relation(fields: [userId], references: [id], onDelete: Cascade)
  @@index([userId])
}
enum MfaKind { TOTP  EMAIL  SMS  BACKUP }

model MfaBackupCode {
  id        String   @id @default(uuid())
  userId    String
  codeHash  String
  usedAt    DateTime?
  createdAt DateTime @default(now())
  @@index([userId])
}

model MfaChallenge {
  id            String   @id @default(uuid())
  userId        String
  sessionSeed   String   // carries "remember me" and pending session flags
  factorId      String?
  attempts      Int      @default(0)
  expiresAt     DateTime
  createdAt     DateTime @default(now())
  @@index([userId])
}

// --- Access-token JTI revocations (small; auto-expired) ---
model TokenRevocation {
  jti       String   @id
  expiresAt DateTime
  @@index([expiresAt])
}

// --- Invitations (admin creates account → email link → user sets password) ---
model Invitation {
  id           String   @id @default(uuid())
  email        String
  role         Role     @default(USER)     // reuse the enum for M1–M5
  tokenHash    String   @unique
  invitedById  String
  invitedAt    DateTime @default(now())
  expiresAt    DateTime
  acceptedAt   DateTime?
  revokedAt   DateTime?
  @@index([email])
}

// --- API keys (M5) ---
model ApiKey {
  id          String   @id @default(uuid())
  name        String
  prefix      String                          // shown in UI, e.g. 'cfv_live_ab12'
  hash        String                          // Argon2 of full secret
  scopesJson  Json                            // ["pdf:read","pdf:write",...]
  createdById String
  createdAt   DateTime @default(now())
  lastUsedAt  DateTime?
  revokedAt   DateTime?
  expiresAt   DateTime?
  @@index([createdById])
}

// --- Rate limiting (sliding window buckets) ---
model RateLimitBucket {
  bucketKey  String   @id                     // 'login:ip:1.2.3.4:2026-08-03T09:35'
  count      Int      @default(0)
  expiresAt  DateTime
  @@index([expiresAt])
}

// --- Security events (dashboard tail; higher-severity subset) ---
model SecurityEvent {
  id          String   @id @default(uuid())
  at          DateTime @default(now())
  kind        String                          // 'REFRESH_REUSE', 'NEW_DEVICE', 'RATE_LIMITED', ...
  userId      String?
  ipAddress   String?
  contextJson Json?
  @@index([at])
  @@index([kind, at])
}

// --- Email outbox (async retriable send) ---
model MailOutbox {
  id             String   @id @default(uuid())
  toAddr         String
  template       String
  varsJson       Json
  attempts       Int      @default(0)
  nextAttemptAt  DateTime @default(now())
  sentAt         DateTime?
  failedAt       DateTime?
  lastError      String?
  @@index([sentAt, nextAttemptAt])
}

// --- Password-breach cache (HIBP k-anon) ---
model BreachCache {
  prefix    String   @id                       // SHA1[:5]
  body      String   @db.Text
  cachedAt  DateTime @default(now())
}
```

### B.0.4 Tables to LEAVE ALONE (v1 territory)

`Conversation`, `ConversationParticipant`, `Message`, `MessageReaction`, `Favorite`, `FileActivity`, `FileMetadata`, `AIAnalysisCache` — v1 app's feature sprawl. v2 doesn't read or write these. Prisma still owns them for v1's sake unless v1 is retired (see §C.0).

## B.1 · High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  Next.js frontend (App Router, client components, cookie-mode)  │
└─────────────────────┬───────────────────────────────────────────┘
                      │  HTTPS (via reverse proxy)
┌─────────────────────▼───────────────────────────────────────────┐
│                    FastAPI app process                          │
│                                                                 │
│  Middleware stack (bottom → top):                               │
│    RequestID → StructuredLogging → CORS → RateLimit →           │
│    SessionCookie → AuthContext → CSRF (cookie mode) →           │
│    PermissionCheck (per-endpoint dep) → RouteHandler            │
│                                                                 │
│  Domain services (all in-process, thin objects):                │
│    AuthService · TokenService · SessionService · MfaService     │
│    PasswordService · PolicyEngine · PermissionResolver          │
│    AuditLogger · Notifier · MailQueue · RateLimiter · RiskEng.  │
│                                                                 │
│  Background tasks (asyncio):                                    │
│    MailWorker · AuditFlusher · SessionCleaner · TokenCleaner    │
│    AuditChainVerifier (nightly) · BreachCacheRefresher          │
└─────────────────────┬───────────────────────────────────────────┘
                      │
                      ▼
                ┌───────────────────────┐
                │  Postgres (existing)  │
                │  192.168.70.10:5432   │
                │  DB: clientfiles      │
                │  Schema: Prisma-owned │
                └───────────────────────┘
```

**Key architectural choices:**

1. **Everything in one process.** No new services, no message queue, no Redis. asyncio for background workers, Postgres for durable state, in-memory dicts for caches.
2. **Schema authority: Prisma.** All schema changes go through the v1 `backend/prisma/schema.prisma` file + `prisma migrate`. The Python side reads/writes via `asyncpg` with raw SQL (no ORM). This keeps one source of truth for the DB shape and avoids two migration systems fighting.
3. **Domain services are thin classes** — no repository/service split unless it earns its keep. `AuthService.login(...)` calls Postgres via an `asyncpg` connection pool. Add a repository layer only when there are ≥3 storage backends.
3. **JWT for access, opaque for refresh.** Access tokens can be verified without a DB hit; refresh tokens need one. This is the standard shape and avoids both "DB hit on every request" and "no revocation possible."
4. **RBAC is authoritative for M1–M5.** ABAC is a pluggable second gate that returns `not-applicable` when no policy matches; the whole engine can be swapped in later without touching route code.

## B.2 · Component Inventory

| Component | File | Responsibility |
|---|---|---|
| `AuthService` | `auth/auth_service.py` | Login, logout, refresh, verify-email, invite-accept |
| `TokenService` | `auth/tokens.py` | JWT sign/verify, refresh-token generate/rotate/revoke, JWKS management, `ver` cache |
| `SessionService` | `auth/sessions.py` | Session lifecycle, device metadata, "logout all", session list |
| `MfaService` | `auth/mfa.py` | Enroll/verify TOTP, email OTP send/verify, SMS OTP adapter, backup codes, challenge tokens |
| `PasswordService` | `auth/passwords.py` | Hash/verify (Argon2id), complexity check, HIBP k-anon, history check, expiration policy |
| `PolicyEngine` | `authz/policy.py` | ABAC policy load, compile, evaluate (deferred beyond design) |
| `PermissionResolver` | `authz/permissions.py` | Resolve user → role set → permission set with `implies[]` closure and cache |
| `RateLimiter` | `security/rate_limit.py` | Sliding-window counter keyed by (category, key). SQLite-backed with in-memory front-cache. |
| `RiskEngine` | `security/risk.py` | Cheap heuristic (new IP/UA) for M4; pluggable model interface for later |
| `AuditLogger` | `audit/logger.py` | Append audit rows to the queue; the flusher writes with hash chaining |
| `Notifier` | `notify/notifier.py` | Route events to channels per rule; email always; webhook + Slack in M6 |
| `MailQueue` | `notify/mail_queue.py` | Persisted email outbox + async worker |
| `SecureRandom` | `common/random.py` | Wraps `secrets` for tokens; enforces min entropy per use |
| `CryptoBox` | `common/crypto.py` | Symmetric encryption (Fernet or NaCl secretbox) for at-rest secrets like TOTP seeds |

## B.3 · Data Model

**See §B.0 for the reused-vs-added schema against the existing Prisma file.** The subsections below (B.3.1–B.3.8) are kept as the from-scratch reference — where a table already exists in Prisma with a subset of these fields, treat the block below as *the target shape after B.0.3 migrations land*, not as a fresh CREATE TABLE. Names use SQLite-era snake_case; the Prisma migrations use camelCase per the existing schema convention.

### B.3.1 Core auth

```
users
  id                UUID PK
  email             TEXT UNIQUE NOT NULL     -- lowercased
  email_normalized  TEXT UNIQUE NOT NULL     -- for lookup; indexed
  email_verified_at TEXT NULL
  display_name      TEXT NULL
  avatar_url        TEXT NULL
  time_zone         TEXT NOT NULL DEFAULT 'UTC'
  locale            TEXT NOT NULL DEFAULT 'en-US'
  status            TEXT NOT NULL            -- INVITED|ACTIVE|SUSPENDED|DEACTIVATED|SOFT_DELETED
  ver               INTEGER NOT NULL DEFAULT 1  -- bumped on role change / force logout
  password_hash     TEXT NULL                -- Argon2id encoded string
  password_updated_at TEXT NULL
  mfa_required      INTEGER NOT NULL DEFAULT 0
  created_at        TEXT NOT NULL
  last_login_at     TEXT NULL
  deleted_at        TEXT NULL                -- soft delete tombstone

password_history
  id                UUID PK
  user_id           UUID FK → users
  password_hash     TEXT NOT NULL
  created_at        TEXT NOT NULL
  INDEX (user_id, created_at DESC)
  -- pruned to last 12 per user

sessions
  id                UUID PK
  user_id           UUID FK → users
  refresh_token_hash TEXT NOT NULL           -- Argon2 hash of opaque token
  parent_id         UUID NULL                -- refresh-token family lineage
  device_label      TEXT NULL                -- user-editable
  user_agent        TEXT NOT NULL
  ip                TEXT NOT NULL
  created_at        TEXT NOT NULL
  last_used_at      TEXT NOT NULL
  expires_at        TEXT NOT NULL
  remember_me       INTEGER NOT NULL DEFAULT 0
  revoked_at        TEXT NULL
  INDEX (user_id, revoked_at)

access_token_revocations           -- small; entries live at most access_ttl
  jti               TEXT PK
  expires_at        TEXT NOT NULL
```

### B.3.2 MFA

```
mfa_factors
  id                UUID PK
  user_id           UUID FK → users
  kind              TEXT NOT NULL            -- TOTP | EMAIL | SMS | BACKUP
  label             TEXT NULL                -- "Phone", "Authy on iPhone"
  secret_encrypted  BLOB NULL                -- CryptoBox-sealed TOTP seed or phone/email addr
  created_at        TEXT NOT NULL
  last_used_at      TEXT NULL
  revoked_at        TEXT NULL

mfa_backup_codes
  id                UUID PK
  user_id           UUID FK
  code_hash         TEXT NOT NULL            -- Argon2
  used_at           TEXT NULL

mfa_challenges                       -- short-lived login step token
  id                UUID PK
  user_id           UUID FK
  session_seed      TEXT NOT NULL            -- carries flags for eventual session issue
  factor_id         UUID NULL                -- last chosen factor
  attempts          INTEGER NOT NULL DEFAULT 0
  expires_at        TEXT NOT NULL
```

### B.3.3 Authorization

```
roles
  id                UUID PK
  name              TEXT UNIQUE NOT NULL     -- 'admin', 'operator', 'viewer', ...
  is_system         INTEGER NOT NULL DEFAULT 0
  description       TEXT NULL
  created_at        TEXT NOT NULL

permissions
  id                UUID PK
  resource          TEXT NOT NULL            -- 'pdf', 'pc', 'user', 'audit', ...
  action            TEXT NOT NULL            -- 'read', 'write', 'delete', 'manage'
  UNIQUE (resource, action)

permission_implies
  parent_id         UUID FK → permissions
  child_id          UUID FK → permissions
  PRIMARY KEY (parent_id, child_id)

role_permissions
  role_id           UUID FK
  permission_id     UUID FK
  PRIMARY KEY (role_id, permission_id)

user_roles
  user_id           UUID FK
  role_id           UUID FK
  granted_by        UUID NULL
  granted_at        TEXT NOT NULL
  PRIMARY KEY (user_id, role_id)

policies                              -- ABAC, design-only in M1–M5
  id                UUID PK
  name              TEXT UNIQUE
  effect            TEXT NOT NULL            -- permit | deny
  target_json       TEXT NOT NULL            -- {resource, action}
  condition_dsl     TEXT NOT NULL
  priority          INTEGER NOT NULL
  enabled           INTEGER NOT NULL DEFAULT 1

api_keys
  id                UUID PK
  name              TEXT NOT NULL
  prefix            TEXT NOT NULL            -- shown in UI, e.g. 'cfv_live_ab12'
  hash              TEXT NOT NULL            -- Argon2 of full secret
  scopes_json       TEXT NOT NULL
  created_by        UUID FK → users
  created_at        TEXT NOT NULL
  last_used_at      TEXT NULL
  revoked_at        TEXT NULL
  expires_at        TEXT NULL
```

### B.3.4 Teams (design-only in M1–M5)

```
teams
  id, name, created_at

team_members
  team_id, user_id, role_id NULL, joined_at
  PRIMARY KEY (team_id, user_id)
```

### B.3.5 Invitations & recovery

```
invitations
  id              UUID PK
  email           TEXT NOT NULL
  role_id         UUID FK
  token_hash      TEXT NOT NULL     -- Argon2
  invited_by      UUID FK
  invited_at      TEXT NOT NULL
  expires_at      TEXT NOT NULL
  accepted_at     TEXT NULL
  revoked_at      TEXT NULL

password_resets
  id              UUID PK
  user_id         UUID FK
  token_hash      TEXT NOT NULL
  created_at      TEXT NOT NULL
  expires_at      TEXT NOT NULL
  used_at         TEXT NULL

email_verifications
  id              UUID PK
  user_id         UUID FK
  token_hash      TEXT NOT NULL
  created_at      TEXT NOT NULL
  expires_at      TEXT NOT NULL
  used_at         TEXT NULL
```

### B.3.6 Security & audit

```
login_attempts
  id            INTEGER PK AUTOINCREMENT
  at            TEXT NOT NULL
  email         TEXT NULL
  user_id       UUID NULL
  ip            TEXT NOT NULL
  ua            TEXT NOT NULL
  outcome       TEXT NOT NULL          -- ok | bad_password | no_user | locked | mfa_fail | mfa_ok
  reason        TEXT NULL
  INDEX (email, at DESC), INDEX (ip, at DESC)

lockouts
  user_id       UUID PK
  locked_until  TEXT NOT NULL
  strike_count  INTEGER NOT NULL

rate_limits                            -- sliding window buckets
  bucket_key    TEXT NOT NULL          -- 'login:ip:1.2.3.4:2026-08-03T09:35'
  count         INTEGER NOT NULL
  expires_at    TEXT NOT NULL
  PRIMARY KEY (bucket_key)

audit_events
  id            UUID PK
  at            TEXT NOT NULL
  actor_id      UUID NULL
  actor_type    TEXT NOT NULL          -- user|service|anonymous|system
  action        TEXT NOT NULL          -- LOGIN_SUCCESS, ROLE_ASSIGNED, ...
  target_type   TEXT NULL              -- 'user', 'pdf', ...
  target_id     TEXT NULL
  ip            TEXT NULL
  user_agent    TEXT NULL
  request_id    TEXT NOT NULL
  outcome       TEXT NOT NULL          -- success|failure|denied
  severity      TEXT NOT NULL          -- INFO|WARN|SEC
  context_json  TEXT NOT NULL          -- redacted structured
  prev_hash     TEXT NOT NULL
  hash          TEXT NOT NULL
  INDEX (at DESC), INDEX (actor_id, at), INDEX (action, at)

security_events                        -- higher-severity subset copy for the dashboard
  id, at, kind, ip, user_id, context_json
  INDEX (at DESC), INDEX (kind, at DESC)

breach_cache                           -- optional; cache HIBP responses to reduce network calls
  prefix        TEXT PK                -- SHA1[:5]
  body          TEXT NOT NULL
  cached_at     TEXT NOT NULL
```

### B.3.7 Notifications / mail

```
mail_outbox
  id            UUID PK
  to_addr       TEXT NOT NULL
  template      TEXT NOT NULL
  vars_json     TEXT NOT NULL
  attempts      INTEGER NOT NULL DEFAULT 0
  next_attempt_at TEXT NOT NULL
  sent_at       TEXT NULL
  failed_at     TEXT NULL
  last_error    TEXT NULL

notify_rules                           -- (M6)
  id            UUID PK
  event_type    TEXT NOT NULL
  channel       TEXT NOT NULL          -- email|webhook|slack
  target        TEXT NOT NULL          -- email addr, webhook URL, slack channel
  enabled       INTEGER NOT NULL
```

### B.3.8 Feature flags (design-only)

```
feature_flags
  key           TEXT PK
  enabled       INTEGER NOT NULL
  rollout_json  TEXT NULL              -- percent, user list, role list
  updated_at    TEXT NOT NULL
```

## B.4 · API Surface

Base path: `/api/v1`. All list endpoints support `page`, `size`, `filter`, `sort` per FR-API-03/04/05.

### Auth

```
POST   /auth/login                       body: {email, password, remember?} → {access, expires_in} + Set-Cookie: refresh
POST   /auth/mfa/verify                  body: {challenge, factor_id, code}   → {access, expires_in} + Set-Cookie
POST   /auth/refresh                     cookie: refresh → new access + rotated refresh cookie
POST   /auth/logout                      revoke current session
POST   /auth/logout-all                  revoke every session for me
POST   /auth/password/forgot             body: {email} → 202 always
POST   /auth/password/reset              body: {token, new_password}
POST   /auth/password/change             body: {current, new}
POST   /auth/email/verify                body: {token}
POST   /auth/email/resend                body: {}    (rate-limited)
GET    /auth/me                          → user profile + permissions[]
```

### MFA

```
GET    /auth/mfa/factors                 my factors
POST   /auth/mfa/totp/enroll             → {secret, qr_uri}
POST   /auth/mfa/totp/verify             body: {factor_id, code}
POST   /auth/mfa/email/enroll            body: {}
POST   /auth/mfa/sms/enroll              body: {phone}    (feature-flagged)
POST   /auth/mfa/backup/regenerate       → new 10 codes
DELETE /auth/mfa/factors/{id}
```

### Users (admin)

```
GET    /users                            list, filterable
POST   /users                            create; sends invite
GET    /users/{id}
PATCH  /users/{id}                       update profile
POST   /users/{id}/suspend               body: {reason}
POST   /users/{id}/reactivate
POST   /users/{id}/force-reset
DELETE /users/{id}                       soft delete
DELETE /users/{id}?hard=true             hard delete (admin + double confirm)
POST   /users/{id}/roles                 body: {role_id}
DELETE /users/{id}/roles/{role_id}
POST   /users/{id}/mfa/reset             admin support: revoke user's MFA
```

### Roles / permissions

```
GET    /roles
POST   /roles                            body: {name, permission_ids[]}
GET    /roles/{id}
PATCH  /roles/{id}
DELETE /roles/{id}                       (not system roles)

GET    /permissions                      grouped by resource
```

### Sessions & devices

```
GET    /sessions                         my sessions
DELETE /sessions/{id}                    revoke one
PATCH  /sessions/{id}                    label a device
```

### API keys (M5)

```
GET    /api-keys                         mine
POST   /api-keys                         → returns full secret ONCE
DELETE /api-keys/{id}
```

### Audit / security

```
GET    /audit/events                     admin; filterable
GET    /audit/events.csv                 admin; streamed
GET    /security/events                  admin
GET    /security/lockouts                admin
POST   /security/lockouts/{user_id}/clear admin
```

### System

```
GET    /system/settings                  admin
PATCH  /system/settings                  admin
GET    /system/health
GET    /system/metrics                   admin (Prometheus text)
```

### Error envelope (RFC 9457)

```json
{
  "type": "https://cfv/errors/rate-limited",
  "title": "Too many requests",
  "status": 429,
  "detail": "You have exceeded 5 attempts in 15 minutes.",
  "instance": "/api/v1/auth/login",
  "code": "AUTH_RATE_LIMIT",
  "retry_after": 823
}
```

## B.5 · Token Strategy

**Access token** — JWT (EdDSA/Ed25519).

```
Header:  {alg: "EdDSA", kid: "k-2026-08"}
Claims:  {
  iss: "cfv",
  aud: "cfv-app",
  sub: user_id,
  sid: session_id,
  jti: uuidv7,
  iat, exp,                          -- exp = iat + 15m
  ver: user.ver,
  roles: ["operator"],               -- name only; for coarse routing
  scp: null                          -- present on API-key tokens
}
```

Verification order:
1. Signature + `iss` + `aud`
2. `exp`
3. `jti` not in `access_token_revocations`
4. `ver` matches current user (cached; misses hit DB)
5. session `sid` not revoked (cached with short TTL)

**Refresh token** — opaque 256-bit random, base64url. Stored client-side as `Set-Cookie: __Host-refresh; HttpOnly; Secure; SameSite=Lax; Path=/`. Server keeps Argon2 hash + `session_id + parent_id` (family lineage).

Rotation rules:
- Every refresh returns a new refresh token; old one is marked used.
- Presenting an already-used refresh token → **entire family revoked**, security event fired.

**Key rotation**:
- Signing keys are Ed25519. Public keys published at `/api/v1/auth/jwks` with `kid`.
- New key introduced with 24h overlap where both `kid`s verify. Then old retired.

## B.6 · Session Cookie Strategy

- One cookie: `__Host-refresh`. No session cookie holds the access token.
- Frontend keeps access token in memory (JS module scope), not localStorage.
- On refresh (silent, on 401 or on interval), the app calls `/auth/refresh` → new access token in body, new refresh cookie set by server.
- On tab focus after long idle: refresh once, then continue.

## B.7 · MFA Design

**TOTP**:
- 20-byte random secret → base32 → `otpauth://totp/CFV:{email}?secret=...&issuer=CFV&period=30&digits=6&algorithm=SHA1`
- Server accepts `-1, 0, +1` step window to tolerate clock skew.
- Secret stored `CryptoBox`-sealed with a key derived from the app master key.

**Email OTP**:
- 6-digit random, 10-min TTL.
- Delivered via `MailQueue`. On network delay the challenge remains valid.

**SMS OTP**:
- Adapter interface `SmsSender.send(to, msg)`; concrete `TwilioSmsSender` and `LoggingSmsSender` (test).
- Behind `feature_flags.mfa_sms`.

**Backup codes**:
- 10 codes, format `XXXX-XXXX` (letters+digits, ambiguous chars removed).
- Argon2-hashed. One-time use. Regeneration invalidates all previous.

**Challenge flow**:
1. `/auth/login` OK, MFA required → returns `{mfa_challenge: <opaque>, factor_options: [...]}`; no tokens yet.
2. `/auth/mfa/verify` with `challenge + factor_id + code` → issues tokens.
3. Challenge is single-use, 5-minute TTL, 5-attempt cap.

## B.8 · Password Design

- **Argon2id** via `argon2-cffi`. Parameters (M1): `t=3, m=64MiB, p=4`. Re-evaluated at each major release; on verify, if parameters have moved, rehash and update the row.
- **Complexity**: length ≥ 12, ≥ 3 of 4 character classes, `zxcvbn` score ≥ 3.
- **HIBP**: SHA-1 of password → first 5 chars → `GET https://api.pwnedpasswords.com/range/{prefix}` (+ Padding header) → check suffix in response body. Timeout 3s; on timeout log warning and pass (fail-open with audit `PASSWORD_BREACH_CHECK_UNAVAILABLE`).
- **History**: verify new-password Argon2 against last 12 hashes; reject if match. Bounded time budget (loop cap + parallelism cap to prevent DoS).
- **Expiration**: policy value in `system_settings`. If set, `password_updated_at + N < now` → response includes `password_expired: true`; endpoints requiring elevated actions block until password changed. UI shows banner ≥ 7d before expiry.
- **Password change / reset** always revokes existing sessions.

## B.9 · Authorization

### RBAC resolver

```
resolve(user_id) -> Set[permission]:
    role_ids = user_roles WHERE user_id
    perm_ids = role_permissions WHERE role_id IN role_ids
    perm_ids ∪= transitive-implies(perm_ids)   # cycle-guarded BFS
    return {(resource, action) for id in perm_ids}
```

Cached in memory per (user_id, user.ver) with LRU (max 5000 users).

### Endpoint decoration (FastAPI)

```
@router.get("/pdfs")
def list_pdfs(_=Depends(require("pdf:read"))): ...
```

`require("pdf:read")` yields a dependency that:
1. Loads `AuthContext` from token or API key.
2. Denies if anonymous.
3. Checks `pdf:read ∈ context.permissions` (RBAC).
4. If any ABAC policies target `pdf/read`, evaluates them; explicit `deny` beats `permit`.
5. Writes an audit row on denial with the requested permission.

### ABAC policy DSL (design)

```
policy "own-team-only":
  effect: permit
  target: {resource: "pdf", action: "read"}
  condition:
    resource.owner_team_id == subject.team_id
  priority: 100
```

Simple JSON expression tree at eval time. Not built in M1–M5.

## B.10 · Security Controls Mapping

| Attack | Mechanism | Where |
|---|---|---|
| Credential brute force | `RateLimiter` on login (IP + email) + `lockouts` | FR-SEC-01/02 |
| Credential stuffing (design) | Global velocity monitor + HIBP block | M6 |
| CSRF (cookie mode) | Double-submit token, `SameSite=Lax` | FR-SEC-04 |
| XSS | JSON responses; strict CSP on HTML page | FR-SEC-06 |
| SQL injection | Parameterized queries + CI grep | FR-SEC-07 |
| Session fixation | Rotate refresh cookie on MFA pass | FR-SEC-09 |
| Session hijacking | Short access TTL + refresh-family revocation | FR-AUTH-04 |
| Token theft (XSS) | Access token in memory, refresh in `HttpOnly` cookie | FR-AUTH-14 |
| Replay | JWT `jti` + short TTL + revocation set | FR-SEC-13 |
| Timing side channel | Constant-time compare; fixed-time login path | FR-SEC-08 |
| Enumeration | Uniform `202` on forgot-password, `401` on login | FR-AUTH-16, FR-PWD-03 |
| Password reuse | History N=12 + HIBP | FR-PWD-06/08 |
| Insider tamper | Audit hash chain + nightly verifier | FR-AUD-03 |

## B.11 · Rate-Limit Taxonomy

Sliding window (60s / 900s / 3600s buckets) keyed by category + key.

| Category | Key | Limit | Action on breach |
|---|---|---|---|
| `login` | `ip:{ip}` | 5 / 15m | 429 + retry-after |
| `login` | `email:{email}` | 10 / 15m | 429 |
| `mfa` | `user:{id}` | 5 / 15m | 429 + challenge revoked |
| `password_forgot` | `email:{email}` | 3 / 1h | 429 silently (still 202 externally) |
| `password_forgot` | `ip:{ip}` | 20 / 1h | 429 |
| `email_verify_resend` | `user:{id}` | 3 / 15m | 429 |
| `api_read` | `user:{id}` | 300 / 1m | 429 |
| `api_write` | `user:{id}` | 60 / 1m | 429 |
| `admin_write` | `user:{id}` | 60 / 1m | 429 + `SEC_ADMIN_THROTTLED` |

## B.12 · Risk Scoring (M4 cheap version; ML-scored later)

Feature vector at login attempt: `is_new_ip_prefix`, `is_new_ua_hash`, `is_odd_hour`, `is_impossible_travel`, `recent_failures`.

Rules:
- Any new (IP prefix / UA hash) → `SECURITY_ALERT` email.
- New IP + new UA + 3+ recent failures → require MFA even if optional; log `RISK_ELEVATED`.

Model interface exposed for later.

## B.13 · Audit Event Schema

Every event:
- Written to `audit_events` via `AuditLogger.emit(event)`.
- Emit is non-blocking; puts row in a queue; a background flusher persists in batches (with hash chaining still deterministic — batch sorts by `at, id`, then chains sequentially).
- **Hash chain**: `hash = SHA256(prev_hash || canonical_json(row - {hash}))`. Genesis hash is 32 zero bytes.
- Nightly verifier walks the chain and writes a `AUDIT_CHAIN_OK/FAIL` audit row.

Standard action codes (M1–M5):
```
AUTH_LOGIN_SUCCESS, AUTH_LOGIN_FAILURE, AUTH_LOGOUT, AUTH_LOGOUT_ALL,
AUTH_TOKEN_REFRESH, AUTH_REFRESH_REUSE_DETECTED,
AUTH_PASSWORD_RESET_REQUESTED, AUTH_PASSWORD_RESET,
AUTH_PASSWORD_CHANGED, AUTH_PASSWORD_EXPIRED_BLOCK,
AUTH_EMAIL_VERIFY_SENT, AUTH_EMAIL_VERIFY_SUCCESS,
AUTH_MFA_ENROLLED, AUTH_MFA_VERIFY_SUCCESS, AUTH_MFA_VERIFY_FAILURE,
AUTH_MFA_RESET_BY_ADMIN,
USER_INVITED, USER_ACCEPTED_INVITE, USER_SUSPENDED, USER_REACTIVATED,
USER_SOFT_DELETED, USER_HARD_DELETED, USER_FORCE_RESET,
ROLE_ASSIGNED, ROLE_REVOKED, ROLE_CREATED, ROLE_UPDATED, ROLE_DELETED,
PERMISSION_DENIED,
API_KEY_CREATED, API_KEY_REVOKED, API_KEY_USED_FIRST,
SEC_LOCKOUT_STARTED, SEC_LOCKOUT_CLEARED, SEC_RATE_LIMITED,
SEC_ALERT_NEW_DEVICE, SEC_RISK_ELEVATED, SEC_ADMIN_THROTTLED,
AUDIT_CHAIN_OK, AUDIT_CHAIN_FAIL,
SYSTEM_SETTINGS_CHANGED
```

## B.14 · Notifications

- All go through `Notifier.publish(event)`. Router looks up `notify_rules` (M6) plus hard-coded rules for security-critical events (which always email the user regardless of rule).
- Channels are pluggable via `Channel` protocol.
- Email path uses `MailQueue`; other channels are direct HTTP with retry.

## B.15 · Admin Surfaces

Two frontend pages (both behind `admin:read` / `admin:write`):

1. **`/admin/users`** — list, search, filters, row-click drawer for edit + role assignment + force-reset + suspend + delete.
2. **`/admin/security`** — 4 stat tiles (active sessions, failed logins today, active lockouts, MFA coverage %), audit table with filters + CSV export, security events tail, "Clear lockout" and "Kill session" buttons.

Also a `/admin/settings` — MFA global policy, password expiration policy, session TTLs.

## B.16 · Threat Model Summary

**In-scope adversaries:**
- Unauthenticated attacker on LAN — mitigated by TLS + auth + rate limits.
- Malicious insider (existing operator user) — mitigated by RBAC + audit + role-change requires admin + hash chain.
- Compromised admin credential — mitigated by MFA (required for admins by default in M3+), audit alerting.

**Not in scope (M1–M5):**
- Physical access to the server.
- Kernel-level attacker.
- Advanced cryptographic attacks on Argon2id / Ed25519.

## B.17 · Observability

- **Logs**: JSON to stdout; picked up by systemd + rotated.
- **Metrics**: `/system/metrics` exposes counters (`auth_login_total{outcome}`, `auth_mfa_challenge_total`, `audit_rows_total{severity}`, `rate_limit_blocked_total{category}`) + histograms (`auth_login_duration_seconds`).
- **Traces**: correlation ID (`X-Request-ID`) generated in middleware, propagated to logs + audit. OTLP export deferred.

## B.18 · Deployment

- **Env variables**: `AUTH_JWT_PRIVATE_KEY_PEM`, `AUTH_JWT_KID`, `AUTH_JWT_PUBLIC_KEYS_JSON` (jwks), `AUTH_MASTER_KEY` (for CryptoBox), `AUTH_HIBP_ENABLED`, existing SMTP vars, `ALLOWED_ORIGINS`.
- **Reverse proxy** (Caddy/nginx) terminates TLS, sets `X-Forwarded-For`, `X-Forwarded-Proto`.
- **DB migrations** are owned by the v1 app's Prisma workflow (`pnpm prisma migrate dev` in dev, `prisma migrate deploy` in prod). v2 does **not** run migrations — it consumes the schema.
- **Backups**: standard `pg_dump` cron on the Postgres host. Extend to include `AuditLog` in its own logical dump for the 7-year retention on security events.

## B.19 · Testing Strategy

- **Unit** for `PasswordService`, `TokenService`, `PermissionResolver`, `RateLimiter`, `MfaService`, `AuditLogger` hash chain.
- **Integration** with a real Postgres schema (fresh per-test schema on the existing DB, `SET search_path` isolates) for each endpoint under `/auth/*` and `/users/*` — covering happy path + every rejection branch.
- **Security-property tests**:
  - Login response time variance ≤ 20ms across (bad email / bad password / unverified) — Locust or pytest-benchmark.
  - No plaintext password appears in logs, DB, or responses (fuzzer that plants sentinel password and greps outputs).
  - Refresh reuse triggers family revocation.
  - Rate limits actually rate-limit (integration).
- **E2E** with Playwright: full login → MFA enrollment → refresh → logout in a headed browser against the running app.
- **Fuzzing**: Hypothesis property tests on the password complexity checker + token verifier.

---

# PART C · Implementation Plan

Six milestones. Each ends at a mergeable state where the app is usable. Ponytail-ordered (highest ROI, lowest risk first). Milestones do **not** map 1:1 to PRs — one milestone may be 3–5 branches.

## C.0 · Coordination with the v1 app (READ FIRST)

Because the DB is shared, v1's auth code and v2's auth code will write the same tables. Three possible worlds — **decision needed from you before M1 starts**:

### World A — v1 is retired

v2 replaces v1 entirely. Chat, Favorites, AI Analysis, FileActivity tables can stay in Postgres (dormant) or be dropped later. v2 owns the auth schema. **Simplest.**

- Pro: no dual-write coordination; hash chain integrity guaranteed; can drop v1's Node auth service.
- Con: v1's users lose Chat, Favorites, AI features. If nobody uses them at LAN scale (per `CLAUDE.md` — they're listed as "v1 sprawl explicitly out of scope"), this is fine.

### World B — v1 stays running, v2 takes over auth

v1's Node backend delegates login/session/token verification to v2 (calls v2's `/api/v1/auth/*`). v1 stops writing directly to `User`, `Session`, `AuditLog`, `PasswordHistory`, `LoginHistory`. v1 reads them freely.

- Pro: v1 features stay alive; single auth surface.
- Con: v1 needs a code change to talk to v2. Not trivial. Adds cross-app dependency.

### World C — v1 and v2 both authenticate independently

Both apps have their own session cookies + login flows against the same `User` table. Only `User.password`, `emailVerified`, `lockedUntil` are shared; sessions are isolated by app (a `sessionType` column or two different tables).

- Pro: minimal v1 disruption.
- Con: audit chain has to skip v1 writes or accept them un-chained; MFA policies split; a password reset in one app doesn't invalidate sessions in the other unless we wire a `ver` bump listener.

### Recommendation

**World A** — retire v1 when v2 reaches feature parity for the pieces you care about (files viewer, PCs, logs, bulk actions, file explorer, search). v1's Chat / AI / Favorites are not in v2's plan; if they're unused, retiring v1 is a big cleanup win.

**If you want to keep v1 running**, pick World B and treat v2's auth surface as a service. Do not do World C — the audit hash chain and MFA policy consistency both break under dual writers.

### Follow-on changes if World A

- Drop or freeze `Conversation*`, `Message*`, `Favorite`, `FileActivity`, `FileMetadata`, `AIAnalysisCache` when convenient (a separate PR — not blocking auth).
- Retire the v1 Node backend service.
- Point the v1 frontend either at v2's API (if kept) or archive it.

### Follow-on changes if World B

- Publish `POST /api/v1/auth/verify` (server-to-server) that v1 calls with a session cookie + gets back `{ user_id, roles, permissions }` or 401.
- Restrict `AuditLog` writes to v2 by DB role (`REVOKE INSERT ON "AuditLog" FROM v1_role`).
- Add a `password_changed` webhook so v1 can drop its own sessions when v2 rotates.



## C.1 · Milestone table

| # | Milestone | Rough scope | Acceptance criteria | Status |
|---|---|---|---|---|
| **M1** | **Auth spine** | Users table, password login, JWT + refresh, sessions, email verify, invite flow, RBAC (admin/operator/viewer), permission decorator on **all existing endpoints**, secure cookies + CSP + CORS + CSRF, RFC 9457 errors, OpenAPI on, login/logout UI, migrate existing single-admin usage | Cannot access any `/api/*` except login/health without a valid token. Admin can invite users. Existing frontend flows all work behind auth. | ✅ SHIPPED |
| **M2** | **Ops-safe basics** | Audit logger + hash chain + queue, rate limiter, brute-force lockout, forgot-password + reset, correlation IDs, structured logs, PASSWORD_CHANGED email, admin can suspend/reactivate/soft-delete users, `/admin/users` list + drawer | 100% of auth actions are audited. Repeated bad logins lock the account. Admin can reset a password via email. | 🚧 branch 29 ✅ audit log; 30/31/32/33 remaining |
| **M3** | **MFA + password hygiene** | TOTP + backup codes, MFA challenge flow, MFA reset by admin, HIBP breach check, password history (12), complexity + zxcvbn, force-reset button, email OTP as second factor option, `/admin/settings` for policies | User can enroll MFA and log in with it. Admin can require MFA globally or per-role. Weak/breached password on set is rejected. |
| **M4** | **Devices + admin dashboard + risk-lite** | Session/device list UI, name devices, revoke individual sessions, "logout all", new-device email, cheap risk score, `/admin/security` dashboard (tiles + audit viewer + CSV export + lockout controls) | User sees active devices and can revoke one. Login from a new device emails an alert. Admin has a working security page. |
| **M5** | **API keys** | API key model + endpoints, scope check parity with RBAC, expiration + revocation, `/admin/api-keys` UI, migrate the CLI/scheduler to auth via API key instead of running unauthenticated | Every service caller uses an API key. Compromise of a key can be revoked in seconds; audit shows key usage. |
| **M6+** | **Backlog (built only when driven)** | SMS OTP adapter, ABAC engine, webhook + Slack notifiers, teams + team-scoped roles, feature flags, idempotency keys, WebAuthn/Passkeys, GraphQL layer, ML-scored risk engine, account merge | Each item unlocked only when a concrete driver appears. Do not build proactively. |

## C.2 · Branch ledger

```
✅ Phase 0                                 create DB, alembic init, initial schema for M1–M5 tables
✅ 22-auth-service-and-db-plumbing         SA Core engine, connection helpers, service classes stubbed
✅ 23-auth-tokens-jwt-refresh              TokenService + refresh family + JWKS endpoint
✅ 24-auth-endpoints-and-openapi-errors    /auth/login /logout /refresh /me + RFC 9457
✅ 25-rbac-decorator-on-existing-endpoints require("pdf:read") on every route from Phases 1–11
✅ 26-frontend-login-and-guarded-routes    /login page, cookie handling, silent refresh
✅ 26.5 SQLite → Postgres port             (off-plan, driven by user) — all app tables migrated
✅ 27-invite-flow-and-email-verify         invite email, first-password-set, email verify, password reset/change
✅ 28-security-headers-cors-csrf           CSP, HSTS, cookie flags, double-submit CSRF, tightened CORS
✅ 29-audit-logger-hash-chained            hash-chain audit rows, wired into service + permissions

✅ 30-rate-limit-and-lockout               sliding-window limiter on auth endpoints; brute-force lockout
✅ 31-correlation-ids                      X-Request-ID middleware; propagate to logs + audit
✅ 32-admin-user-endpoints                 GET/PATCH /users, suspend/reactivate/force-reset/soft-delete
✅ 33-admin-users-page                     Frontend /admin/users list + drawer + invite modal
   ── M2 complete ──
⬜ M3 branches (MFA, HIBP, history, complexity)
⬜ M4 branches (devices UI, dashboard, risk-lite)
⬜ M5 branches (API keys, service caller migration)
```

**Acceptance for M1 rollout — SHIPPED:**
- ✅ One-time script converts the current "no auth" world (`scripts/create_admin.py` — env-driven, idempotent, prints the user id).
- ✅ All existing endpoints from Phases 1–11 are decorated with the correct permission.
- ✅ The frontend renders `/login`, redirects protected routes, and shows the invite flow if invited (`/accept-invite?token=...`).
- ⏭️ Documented rollback: `AUTH_ENFORCED=false` flag deferred — no need, we never shipped a pre-auth version to real users.

## C.3 · Rollout Strategy

1. **Shadow mode** (one release): auth runs but is **not enforced**. Every request is annotated with `would_allow`/`would_deny`; results logged. Admin verifies denials would not have broken anyone.
2. **Enforce**: flip `AUTH_ENFORCED=true`. `would_deny` becomes `deny`. Communication + docs.
3. **Cleanup**: remove the `AUTH_ENFORCED` gate in the release after enforcement lands cleanly.

## C.4 · Risk Register

| Risk | Impact | Mitigation |
|---|---|---|
| Argon2 cost too high on the LAN server → login latency | High | Benchmark on the server before M1; tune `t/m/p` to hit p95 ≤ 300ms. |
| Postgres audit-write contention at scale | Low | Batched flusher (append-only INSERT), single sequential writer, `AuditLog(prevHash)` locking derived from `SELECT ... ORDER BY at DESC LIMIT 1 FOR UPDATE` in the flusher transaction. |
| v1 (Node) writes to `AuditLog` without hash-chaining | High | Once v2 owns the chain, v1 must either stop writing directly OR route its writes through v2's API. See §C.0. Otherwise chain corrupts. |
| v1 and v2 both write to `Session` with different token semantics | High | v2's session shape is a strict superset; if v1 still authenticates, either retire v1's sessions or namespace them (`sessionType` column). See §C.0. |
| Existing endpoints missing a permission decoration | Critical | CI check: scan `@router.*` decorators and fail if any handler is missing a `Depends(require(...))` (or an explicit `@public`). |
| Email delivery down → users cannot reset | Medium | Admin can force-reset in-app; nightly alert if outbox > N. |
| Refresh cookie stolen via subdomain XSS | High | `__Host-` prefix + `SameSite=Lax`; single-domain deployment; CSP disallows inline scripts. |
| Hash chain corruption from crash mid-batch | Medium | Batch is a single SQLite transaction; if it fails, none of it lands. |
| Key rotation done wrong → mass logout | Medium | Documented overlap window; add `kid`s pre-launch and rehearse in staging. |
| ABAC never being built after being designed | Low | Explicitly deferred, not started. RBAC covers all foreseeable decisions. |

## C.5 · Explicit deferrals (what M1–M5 does NOT include)

- SMS OTP (adapter stubbed, factor type recognized; enroll endpoint feature-flagged off)
- Slack, webhook, push notifications
- ABAC evaluation (schema present, engine deferred)
- Teams (schema present, endpoints deferred)
- Feature flags (schema present, no runtime evaluator)
- GraphQL surface
- Idempotency keys
- Account merge
- Anomaly / ML risk engine (only heuristic rules)
- WebAuthn / Passkeys

## C.6 · Dependencies to add

| Package | Purpose | Ponytail justification |
|---|---|---|
| `asyncpg` | Postgres driver (async) | Fast, no ORM baggage, plays nice with FastAPI |
| `argon2-cffi` | Argon2id hashing | Correct hash; stdlib doesn't cover this |
| `pyjwt[crypto]` **or** `authlib` | JWT sign/verify | Standard, one of these; pick pyjwt for smaller surface |
| `cryptography` | Ed25519 keys + Fernet (CryptoBox for TOTP seeds) | Standard toolchain |
| `pyotp` | TOTP RFC 6238 | ~200 lines saved; battle-tested |
| `zxcvbn` (python port) | Password strength scoring | Otherwise we hand-roll a bad approximation |
| `httpx` | HIBP calls | Existing FastAPI transitive |
| `python-multipart` | Form login | Already present |

Deliberately **not** adding: Prisma-python (v2 is self-owned now), Redis (Postgres covers rate-limit + sessions), Celery (asyncio background tasks), the SQLAlchemy ORM (Core-only for schema; queries via `text()` for the same shape we'd have written raw).

Adding for Phase 0 setup:

| Package | Purpose |
|---|---|
| `sqlalchemy[asyncio]>=2.0` | Schema DSL + async engine (Core only, no ORM) |
| `alembic` | Migration tool |
| `asyncpg` | Postgres driver for the async engine |
| `psycopg[binary]` | Sync driver, used only by Alembic's offline/online modes |

## C.7 · Open questions for approval

**Blocking (must answer before code):**

1. **§C.0 world pick** — A (retire v1), B (v1 delegates auth to v2), or C (independent)? Recommendation: A.
2. **Prisma migration ownership** — Do I edit `D:\Code\aw\client-files-viewer\backend\prisma\schema.prisma` directly and run `prisma migrate dev` there, or do you want a copy of the schema colocated with v2? Recommendation: edit v1's schema in place — one source of truth.
3. **Existing user data** — Are there real users in the current `User` table right now (i.e. current password hashes matter)? If yes, migration must preserve them and re-hash on next login if the parameters have moved. If no, we can drop and re-invite.
4. **Role enum mapping** — Confirm the mapping in §B.0.2: `SUPER_ADMIN → all`, `ADMIN → user/data mgmt`, `MANAGER → operator`, `USER → viewer`. Or would you rather rename now (e.g. `MANAGER → OPERATOR`)?

**Preferential (I have a recommendation):**

5. **JWT lib**: `pyjwt` or `authlib`? (Recommendation: `pyjwt`.)
6. **Password expiration policy**: default off, admin-toggleable? (Recommendation: yes, default off.)
7. **MFA policy**: default optional for everyone, or required for admins? (Recommendation: required for admins from M3.)
8. **Milestone ordering** (M1 auth spine → M2 audit + reset → M3 MFA + hygiene → M4 devices + dashboard → M5 API keys → M6+ backlog) — OK?
9. **Named compliance regime coming** (SOC2, HIPAA)? Answer changes retention defaults and audit encryption at rest. Recommendation: assume none until you say otherwise.
10. **Teams in M1** — any notion, even as a static label? Recommendation: no, defer entirely to M6.

## C.8 · Definition of Done (per milestone)

- All acceptance criteria met (integration test asserts each).
- No handler under `/api/v1/*` lacks a permission decoration (CI grep enforces).
- OpenAPI diff reviewed on the PR.
- Audit rows written for every action added in that milestone.
- Security headers pass an integration probe.
- No new dependencies added without listing in `C.6`.
- The `/admin/security` page reflects the new state.
