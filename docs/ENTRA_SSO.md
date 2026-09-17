# Microsoft Entra SSO — Spec & Requirements

**Status:** ✅ **LIVE** — verified end-to-end against the real tenant (login, logout, JIT-provision, MFA hand-off) · **Owner:** Joe · **Created:** 2026-09-16 · **Last updated:** 2026-09-16
**Builds on:** Phase 15 auth (`docs/PHASE15_AUTH.md`) — M1 + M2 shipped.
**Supersedes:** PHASE15 §A.5 / §A.6, which parked external IdPs ("Add as adapters when a customer asks"). This is that adapter.

## What actually shipped

- **S1 — DB + config.** Migration `e5f6a1b2c3d4` (chained off head `d4e5f6a1b2c3`) adds `users.sso_provider/sso_subject/sso_tenant` + partial unique index `ux_users_sso`. `AuthSettings` gained the Entra fields with a **derived `sso_enabled`** — the toggle must be on *and* tenant/client/secret all present, else it logs a warning and stays off (password login always boots). Applied to the live DB.
- **S2 — `auth/sso.py`.** OIDC Auth-Code+PKCE adapter: `pkce_pair`, HMAC-signed state cookie (`sign_/read_state_cookie`, keyed on the existing `refresh_hash_secret`), `build_authorize_url`, `verify_id_token` (PyJWT, RS256, iss/aud/exp/nonce/tenant), async `_exchange_code` (httpx), and the `EntraSso` class (`begin()` / `complete()`). Entra `access_token` is discarded — no Graph calls. **No new deps.**
- **S3 — `AuthService.resolve_sso_user`** (`auth/service.py`) + `AccountDisabled`. Ladder: find-by-`oid` → link-by-email (role preserved, INVITED→ACTIVE) → JIT-create viewer (verified email, NULL password). Audits `SSO_LOGIN_SUCCESS` / `SSO_ACCOUNT_LINKED` / `SSO_USER_PROVISIONED`.
- **Verification:** `scripts/sso_smoke.py` — **25/25** (Part A offline crypto: PKCE, state cookie tamper/expiry, authorize URL, id_token happy + nonce/tenant/expiry/wrong-key; Part B DB: JIT / idempotent / email-link / suspended-deny, with cleanup). App imports clean with SSO off.
- **S4 — routes** (`auth/sso_routes.py`, wired in `api.py`): `GET /sso/config` (public `{enabled}`), `GET /sso/login` (302→Entra + signed `cfv_sso` state cookie), `GET /sso/callback` (verify state → exchange → verify id_token → resolve → `issue_login` → set `cfv_refresh` → 302 `/`). Disabled → `/login`+`/callback` 404. CSRF exemption inherited via the `/api/v1/auth` prefix in `http_security.py`.
- **S5 — login page** (`frontend/app/login/page.tsx`): "Sign in with Microsoft" button gated on `/sso/config`, `sso_error` banner read off the URL (no `useSearchParams`/Suspense dependency), Microsoft four-square glyph. `tsc --noEmit` clean.
- **S6 — DEPLOYMENT.md §4d**: Entra env block + app-registration prereqs + secret-rotation note.
- **Route tests:** SSO off → `config {enabled:false}`, login/callback 404. SSO on → login 302 to `login.microsoftonline.com` with PKCE `S256` challenge + `cfv_sso` cookie; bad-state callback → `302 /login?sso_error=state`.

### Landed after the initial S1–S6 build (same session, now live)

- **Runtime on/off toggle.** `sso_login_enabled` in the `app_settings` table (migration `f6a1b2c3d4e5`, service `auth/app_settings.py`, API `/api/v1/settings`). SSO is usable only when configured in env **and** the toggle is on; `/sso/config`, `/sso/login`, `/sso/callback` all consult it (off → `/login?sso_error=sso_off`). Flipped from the Settings page "Access & Security → Local accounts only" card (admin-only). This is the "block SSO / local-only" break-glass switch from the decisions table.
- **SSO allowlist.** `sso_allowlist` table (migration `b2c3d4e5f7a8`) + `sso_allowlist_enabled` setting. When on, the callback rejects any verified email not on the list — **new or already-provisioned** — with `/login?sso_error=not_allowed` ("contact IT Support"). Case-insensitive; local `/admin/login` accounts are exempt so the bootstrap admin can always manage the list. Endpoints `GET ?q&limit&offset` (search + paginate), `POST {emails:[…]}` (bulk add), `DELETE /{email}`. Managed on the dedicated **`/admin/security`** page (search + pagination + bulk paste — built to scale to large lists). An empty enabled list blocks all SSO (the UI warns). This narrows the earlier "JIT-provision anyone in the tenant" default — decision D3 now gated by the allowlist when enabled. (If the list ever needs to scale to thousands with zero in-app upkeep, the deferred alternative is Entra security-group gating — see decisions table.)
- **Login UX split.** `/login` is Entra-only with the split-panel America Works brand design (`login-card.tsx` `LoginShell`); local email+password moved to `/admin/login`. Forgot-password link is local-only. `sso_off` added to the `sso_error` messages.
- **RP-initiated logout.** `GET /sso/logout` revokes the CFV session then 302s through Entra `end_session` (so Microsoft drops its session too — matters on shared lab PCs), returning to the post-logout redirect. `MePayload.sso` tells the frontend which logout path to take (SSO users → `/sso/logout`; local users → clear + `/admin/login`). New settings field `AUTH_ENTRA_POST_LOGOUT_REDIRECT_URI` (defaults to `{APP_BASE_URL}/login`) — **must be registered as a post-logout redirect URI** on the app registration.
- **MFA hand-off.** The callback now routes through `AuthService.finish_primary_auth`: if 2FA is required the browser is sent to `/mfa` (signed `cfv_mfa` challenge) instead of straight to `/`. New SSO users therefore enrol 2FA on first login when `mfa_required` is on. See `docs/PHASE15_AUTH.md` M3.
- **Deadlock fix (critical).** The provision→MFA-challenge path self-deadlocked on the audit advisory lock because `resolve_sso_user` audited with the *request* connection. All audit emits now use `emit(None)` (own committed tx). Also, PyJWKClient's blocking JWKS fetch now runs via `anyio.to_thread` so a reset request can't freeze the event loop or leak a DB connection. See the gotcha in `docs/PHASE15_AUTH.md`.

### ⚠️ Production constraint (HTTPS)

Entra only allows `http://localhost` redirect URIs; **any non-localhost redirect URI must be HTTPS.** So the prod `http://192.168.70.180/...` redirect **cannot be registered** — SSO in prod needs TLS in front (reverse proxy + cert → `https://192.168.70.180/api/v1/auth/sso/callback` and `.../login` for post-logout), plus `AUTH_COOKIE_SECURE=true`. Password login over plain HTTP is unaffected; only the SSO button needs HTTPS in prod. Dev works today on `http://localhost:3000/...`.

This is an **additive** layer. It does not replace the existing email+password / JWT / session / RBAC / audit machinery — it plugs a new front door into it. Everything downstream of "user is authenticated" (access tokens, refresh cookie, RBAC, audit, the Users admin page) is reused unchanged.

---

## 1 · Decisions locked (2026-09-16)

| # | Decision | Choice |
|---|---|---|
| D1 | Login modes | **Entra SSO + email/password fallback.** SSO is the primary button; password login stays for break-glass (`admin@aw.local`) and any account that never enrolled in Entra. |
| D2 | Role source | **Default role, admin promotes.** First SSO login lands the user as `viewer`; an app admin promotes to `operator`/`admin` in the existing Users page. **No Entra group/app-role wiring** — keeps the app registration minimal. |
| D3 | Provisioning | **Just-in-time (JIT).** First SSO login auto-creates the user row from the Entra token, linking to an existing row by normalized email if one is already present. |
| D4 | Flow ownership | **Backend-driven OIDC Authorization Code + PKCE.** FastAPI owns the whole OIDC dance and mints the app's *own* session at the end. No MSAL.js in the frontend. (Rationale in §4.) |
| D5 | Token/session model | **Unchanged.** SSO callback ends by calling the existing `AuthService.issue_login(...)`, sets the `cfv_refresh` cookie, and redirects to `/`. The frontend's existing silent-refresh bootstrap picks it up. |

---

## 2 · Goal

Let AmericaWorks staff sign in to Client Files Viewer with their existing Microsoft work accounts (Entra ID / Azure AD), instead of a separate app password, while keeping a local password fallback so an Entra outage or misconfiguration can never lock everyone out.

**Non-goals (this milestone):** SCIM provisioning, SAML, Entra security-group → role mapping, Conditional Access enforcement in-app, guest/B2B external tenants, MFA handled in-app (Entra owns MFA once SSO is on).

---

## 3 · Why backend-driven (D4), in one paragraph

The app already issues its own Ed25519 JWT access tokens + opaque refresh tokens bound to a `sessions` row, keeps the access token in browser memory only, and refreshes silently via the `HttpOnly` `cfv_refresh` cookie (PHASE15 branch 26). If we let the browser talk OIDC directly (MSAL.js), we'd bolt a *second, parallel* token system onto the frontend and have to reconcile Entra tokens with our own session/RBAC/audit model. Backend-driven OIDC avoids all of that: Entra is used **only** to prove identity once, at the callback; from that instant the user is on a normal CFV session and every existing code path (`require(perm)`, audit chain, Users page, logout-all, `ver`-bump revocation) works with zero changes. `issue_login`'s docstring already anticipated this ("complete SSO in the future" — `auth/service.py:674`).

---

## 4 · Flow

```
Browser                     FastAPI (/api/v1/auth/sso/*)              Entra
  |  click "Sign in with Microsoft"                                     |
  |------ GET /sso/login --------------->|                              |
  |                                      | mint state+nonce+PKCE verifier|
  |                                      | stash in signed __Host cookie |
  |<-- 302 to Entra /authorize ----------|                              |
  |------------------------- GET /authorize (code+PKCE challenge) ------>|
  |                                      |         (user signs in, MFA)  |
  |<------------------------ 302 back to /sso/callback?code&state -------|
  |------ GET /sso/callback?code&state ->|                              |
  |                                      | verify state cookie          |
  |                                      | POST /token (code+verifier+secret) --->|
  |                                      |<---- id_token + access_token ----------|
  |                                      | verify id_token (iss/aud/exp/nonce,    |
  |                                      |   sig via Entra JWKS)                   |
  |                                      | JIT provision / link by oid|email      |
  |                                      | AuthService.issue_login(...) -> TokenPair
  |<-- 302 to /  (Set-Cookie: cfv_refresh)-|                            |
  |------ GET /api/v1/auth/refresh ------>|  (existing bootstrap)        |
  |<---- access token (in-memory) --------|                              |
  |  ... normal authenticated app ...     |                              |
```

No access token is ever put in a URL. The callback only sets the refresh cookie and 302s to `/`; the existing `auth-provider` bootstrap does the silent `/refresh` to obtain the in-memory access token.

---

## 5 · Entra app registration (one-time, done by whoever owns the tenant)

Prerequisites the build depends on — capture the values into `.env` (§7):

1. **Register an application** in Entra admin center → *App registrations* → *New registration*.
   - Name: `Client Files Viewer`.
   - Supported account types: **Single tenant** (accounts in this org directory only).
   - Redirect URI: **Web** → `http://192.168.70.180/api/v1/auth/sso/callback` (prod) and `http://localhost:8000/api/v1/auth/sso/callback` (dev). Add both.
2. **Client secret**: *Certificates & secrets* → *New client secret* → copy the **Value** (not the ID) immediately → `AUTH_ENTRA_CLIENT_SECRET`. Note its expiry; rotation is a `.env` edit + service restart.
3. **API permissions**: Microsoft Graph → *Delegated* → `openid`, `profile`, `email`. Grant admin consent. (No Graph calls at runtime — claims come from the ID token. These scopes just populate the token.)
4. **Token configuration** (optional but recommended): add the `email` optional claim to the ID token so users whose UPN ≠ email still resolve.
5. Copy **Directory (tenant) ID** → `AUTH_ENTRA_TENANT_ID` and **Application (client) ID** → `AUTH_ENTRA_CLIENT_ID`.

Redirect URIs must match `AUTH_ENTRA_REDIRECT_URI` in `.env` **exactly** (scheme, host, port, path). When TLS goes in front (DEPLOYMENT §4a note about `AUTH_COOKIE_SECURE`), switch these to `https://…` and re-register.

---

## 6 · Functional Requirements

Numbered `FR-SSO-*`. All testable against a real request/response or the running app.

| ID | Requirement |
|---|---|
| FR-SSO-01 | A `GET /api/v1/auth/sso/login` endpoint builds the Entra `/authorize` URL (Authorization Code + PKCE `S256`), generates `state` + `nonce` + code_verifier, stores them in a short-lived signed `__Host-cfv_sso` cookie (5-min TTL, `HttpOnly`, `SameSite=Lax`), and 302-redirects the browser to Entra. |
| FR-SSO-02 | A `GET /api/v1/auth/sso/callback` endpoint validates the returned `state` against the cookie (constant-time), rejects mismatch/expired/missing with a redirect to `/login?sso_error=state`. |
| FR-SSO-03 | The callback exchanges `code` at the Entra `/token` endpoint using the PKCE `code_verifier` + `client_secret`, over server-side httpx (never in the browser). |
| FR-SSO-04 | The returned `id_token` is fully validated: signature against the tenant JWKS (`…/discovery/v2.0/keys`, cached), `iss == https://login.microsoftonline.com/{tenant}/v2.0`, `aud == client_id`, `exp`/`nbf` within skew (±120s), and `nonce` matches the cookie. Any failure → `/login?sso_error=token`, audited. |
| FR-SSO-05 | Identity is keyed on the immutable `oid` claim (Entra object id) plus `tid` (tenant). Email is used only for the **first** link to an existing local row. |
| FR-SSO-06 | **JIT provisioning:** if no user matches `oid`, and no local row matches the normalized email, create a new user — `role=viewer`, `status=ACTIVE`, `email_verified_at=now()` (Entra vouches for the email), `password_hash=NULL`, `display_name`/`first_name`/`last_name` from token claims — and store the `oid`/`tid` linkage (§8). |
| FR-SSO-07 | **Account linking:** if no `oid` match but a local row matches the normalized email, attach the `oid`/`tid` to that existing row (preserving its role, e.g. the bootstrap admin can bind their Microsoft account). Linking is audited as `SSO_ACCOUNT_LINKED`. |
| FR-SSO-08 | On success the callback calls `AuthService.issue_login(...)` with the resolved `user_id`/`role`/`ver`, sets the `cfv_refresh` cookie exactly as `/login` does (`auth/routes.py:_set_refresh_cookie`), and 302-redirects to `/`. |
| FR-SSO-09 | A `SUSPENDED`, `DEACTIVATED`, or `SOFT_DELETED` user who authenticates via Entra is **denied** (redirect `/login?sso_error=disabled`), same gate as password login. SSO never resurrects a disabled account. |
| FR-SSO-10 | Email+password login (`POST /api/v1/auth/login`) continues to work for accounts with a `password_hash`. SSO-only accounts (`password_hash=NULL`) return the generic 401 on password login (never reveal that the account is SSO-only). |
| FR-SSO-11 | SSO can be toggled by `AUTH_SSO_ENABLED`. When false, the `/sso/*` routes return 404 and the frontend button is hidden. Default: driven by presence of Entra env vars. |
| FR-SSO-12 | The login page shows a **"Sign in with Microsoft"** button (primary) above the existing email/password form. Clicking it navigates to `/api/v1/auth/sso/login`. When `AUTH_SSO_ENABLED=false`, the button is absent. |
| FR-SSO-13 | `sso_error` query values render a friendly, non-leaky banner on `/login` (e.g. "Microsoft sign-in failed — try again or use your password"). No token/tenant internals surfaced to the user. |
| FR-SSO-14 | Every SSO attempt writes an audit row: `SSO_LOGIN_SUCCESS`, `SSO_LOGIN_FAILURE` (with a coarse reason: `state`/`token`/`disabled`), `SSO_USER_PROVISIONED`, `SSO_ACCOUNT_LINKED`. Reuses the existing hash-chained `AuditLogger`. |
| FR-SSO-15 | Logout is unchanged — it revokes the CFV session (`cfv_refresh`). It does **not** attempt Entra single-logout (out of scope; documented). A logged-out user clicking "Sign in with Microsoft" may be silently re-authed by Entra's own session — acceptable. |
| FR-SSO-16 | `/sso/*` routes are exempt from the CSRF double-submit check (same exemption class as the rest of `/api/v1/auth/*`, `auth/http_security.py`). Both are safe-method GETs; `state` is the CSRF defense for the callback. |

---

## 7 · Config additions (`auth/settings.py` + `.env`)

New `AuthSettings` fields (follow the existing `_opt`/`_bool` pattern — no config framework):

```dotenv
# ---- Entra SSO --------------------------------------------------
AUTH_SSO_ENABLED=true
AUTH_ENTRA_TENANT_ID=<Directory (tenant) ID>
AUTH_ENTRA_CLIENT_ID=<Application (client) ID>
AUTH_ENTRA_CLIENT_SECRET="<secret Value>"     # quote it — see .env BOM/backslash trap in DEPLOYMENT §4b.1
AUTH_ENTRA_REDIRECT_URI=http://192.168.70.180/api/v1/auth/sso/callback
# optional, default derived:
AUTH_ENTRA_SCOPES=openid profile email
AUTH_SSO_STATE_TTL=300
```

`load()` should treat SSO as enabled only when `AUTH_SSO_ENABLED` is true **and** tenant/client/secret are all present; otherwise disabled with a one-line warning at startup (don't hard-fail — password login must still boot). Authority base: `https://login.microsoftonline.com/{tenant}` → `/oauth2/v2.0/authorize`, `/oauth2/v2.0/token`, `/discovery/v2.0/keys`.

**No new backend deps.** ID-token validation uses the already-present **PyJWT** (`jwt.PyJWKClient` against the tenant JWKS + `jwt.decode` with `audience`/`issuer`); the code exchange uses the already-present **httpx** (as HIBP does). This satisfies the CLAUDE.md "no new backend deps unless a few lines can't replace them" rule.

---

## 8 · DB migration

One Alembic migration, additive, on the `users` table (schema owned by Alembic per CLAUDE.md):

```python
op.add_column("users", sa.Column("sso_provider", sa.Text, nullable=True))   # "entra"
op.add_column("users", sa.Column("sso_subject",  sa.Text, nullable=True))   # Entra oid
op.add_column("users", sa.Column("sso_tenant",   sa.Text, nullable=True))   # Entra tid
op.create_index(
    "ux_users_sso", "users", ["sso_provider", "sso_subject"], unique=True,
    postgresql_where=sa.text("sso_subject IS NOT NULL"),
)
```

`password_hash` is already nullable (migration `89986958c76a:68`), so SSO-only users need no schema change there. Partial unique index guarantees one CFV account per Entra identity while leaving password-only rows unconstrained.

> **ponytail:** two/three columns on `users` beats a normalized `external_identities` table while there's exactly one IdP. Upgrade path if a second provider ever appears: move these to `external_identities(user_id, provider, subject, tenant)` and drop the columns.

Revision `e5f6a1b2c3d4` chains off the real head `d4e5f6a1b2c3` (the PHASE15 doc's "current head" is stale — notifications/archive/profiles/widgets migrations landed after it).

---

## 9 · Code map (what to add)

```
auth/
  sso.py         NEW — Entra OIDC adapter: authorize-URL builder, PKCE, state
                 cookie sign/verify, code exchange (httpx), id_token verify
                 (PyJWKClient), claim extraction. Pure functions + one small class.
  sso_routes.py  NEW — GET /sso/login, GET /sso/callback. Thin: orchestrates
                 sso.py + AuthService.issue_login + AuditLogger. Registered in
                 api.py behind the AUTH_SSO_ENABLED gate.
  service.py     +resolve_sso_user(conn, claims) — find-by-oid → link-by-email →
                 JIT create. Returns (user_id, role, ver) or raises AccountDisabled.
  settings.py    +Entra fields per §7.
  http_security.py  add /sso/* to the CSRF-exempt prefix set.
scripts/
  sso_smoke.py   NEW — offline: mints a fake id_token with a local key, points the
                 verifier at a stub JWKS, asserts JIT-create / link / disabled-deny
                 / state-mismatch paths. (No live Entra needed for CI.)

frontend/app/
  login/page.tsx           +"Sign in with Microsoft" button + sso_error banner.
  _lib/auth.ts             no change needed (bootstrap silent-refresh already handles the post-callback session).
```

---

## 10 · Security requirements

| ID | Requirement |
|---|---|
| SEC-SSO-01 | `state` and `nonce` are ≥128-bit random (reuse `auth/random.py`); `state` compared constant-time. |
| SEC-SSO-02 | PKCE `S256` mandatory even though the client is confidential (defense in depth against code interception). |
| SEC-SSO-03 | The state cookie is signed (HMAC via the existing `refresh_hash_secret` keying material) so a forged callback can't smuggle attacker-chosen verifier/nonce. `__Host-` prefix, `HttpOnly`, `SameSite=Lax`, `Secure` when `AUTH_COOKIE_SECURE=true`. |
| SEC-SSO-04 | JWKS is fetched over TLS and cached; key rotation handled by `PyJWKClient` (re-fetch on unknown `kid`). Never trust an `alg=none` or HS token — pin to RS256/the tenant's advertised algs. |
| SEC-SSO-05 | The Entra `access_token` returned at the code exchange is **discarded** — we call no Graph APIs. Only the validated `id_token` claims are used. |
| SEC-SSO-06 | Single-tenant lock: reject any `id_token` whose `tid` ≠ `AUTH_ENTRA_TENANT_ID`, even if signature checks out. |
| SEC-SSO-07 | No token, secret, code, or `code_verifier` is ever logged. Audit rows carry only `oid`/email/outcome (consistent with FR-AUD-06 redaction). |

---

## 11 · Edge cases & how they resolve

- **Email changed in Entra:** linkage is by `oid`, not email, so a corporate email change doesn't orphan the account. The stale local email can be reconciled by an admin (or a later "sync display fields on login" nicety — deferred).
- **Two local rows, same email:** the `email_normalized` unique constraint already prevents this; link targets the single row.
- **Local admin binds Microsoft account:** FR-SSO-07 attaches `oid` to the existing `admin@aw.local` row on first SSO login *if the Entra email matches*. If it doesn't (likely — `admin@aw.local` is a synthetic local address), the admin is JIT-created as a *separate* `viewer` and must be promoted, or an admin sets `sso_subject` manually. Documented; acceptable for a 1–3 admin shop.
- **SSO user tries "Forgot password":** they have no `password_hash`; the forgot flow still returns the generic 202 (FR-PWD-03) and simply mails nothing actionable. No leak.
- **Entra down / secret expired:** SSO button 500s or Entra errors; password fallback (D1) keeps admins in. This is the whole reason for D1.

---

## 12 · Milestones / build order

| Step | Deliverable | Done when |
|---|---|---|
| S1 | Migration §8 + `settings.py` fields + startup gate | `alembic upgrade head` clean; app boots with SSO off and on. |
| S2 | `auth/sso.py` (authorize URL, PKCE, state cookie, token exchange, id_token verify) + `sso_smoke.py` | Smoke passes offline against a stubbed JWKS. |
| S3 | `resolve_sso_user` in `service.py` (find/link/JIT/disabled) | Smoke covers all four paths + audit rows land. |
| S4 | `sso_routes.py` wired into `api.py`, CSRF exemption | Real round-trip against the actual tenant lands a logged-in session; `/refresh` bootstrap works. |
| S5 | Frontend button + `sso_error` banner | Click-through on `pnpm dev` against dev redirect URI. |
| S6 | DEPLOYMENT.md note (Entra env vars, redirect URI, secret rotation) | Doc updated; prod redirect URI registered. |

Estimated surface: ~1 new backend module + 1 route file + ~40 lines in `service.py` + 1 migration + a button. No new deps.

---

## 13 · Acceptance / smoke checklist

1. SSO off (`AUTH_SSO_ENABLED=false`): `/sso/login` → 404; login page shows no Microsoft button; password login works.
2. SSO on, brand-new Entra user: click Microsoft → consent → lands on dashboard as **viewer**; `users` row created with `sso_subject` set, `password_hash NULL`, `email_verified_at` set; `SSO_USER_PROVISIONED` + `SSO_LOGIN_SUCCESS` audited.
3. Same user second login: no new row; `SSO_LOGIN_SUCCESS` only.
4. Admin promotes that user to `operator` in Users page → next SSO login carries `operator` perms (via `ver`/role read at `issue_login`).
5. Existing email-matched row: `SSO_ACCOUNT_LINKED`; role preserved.
6. Suspended user via SSO → `/login?sso_error=disabled`, no session.
7. Tampered `state` → `/login?sso_error=state`, `SSO_LOGIN_FAILURE(state)`.
8. Break-glass: with Entra env vars wrong, `admin@aw.local` still logs in by password.

---

## 14 · Out of scope (this milestone)

- Entra security-group / app-role → CFV role mapping (D2 chose manual promotion; revisit if group management is wanted later — it's a claim-read + a mapping table).
- SCIM auto-deprovisioning, SAML, guest/multi-tenant, Conditional Access signals in-app.
- Entra single-logout (RP-initiated logout at `/logout` → Entra). CFV logout is local only (FR-SSO-15).
- In-app MFA for SSO users — Entra owns that once SSO is on.
