# Salesforce Integration — Push Assessments to Client Records

**Status:** Setup / spec (not built yet) · **Owner:** Joe · **Started:** 2026-09-17

## Goal

Let a rep push a renamed assessment PDF (VIA / O*NET) straight from Client Files
Viewer onto the correct **Person Account's Files** in the PACE org, instead of
downloading it and manually uploading in Salesforce.

## How the pieces map

- **Match key:** the **Case Number** on the Salesforce **Assignment** object. It's
  unique to the client and identical across all of that client's assignments
  (active + terminated), so `Case Number → Assignment → Account` resolves 1:1.
- **The PDF only carries the client's *name*** (vendor-generated), not the case
  number — so a rep supplies the case number **once per client**; the app stores
  the mapping (name ↔ case number ↔ Account Id) and auto-routes every assessment
  after that.
- **Upload:** insert a `ContentVersion` with `FirstPublishLocationId = <Account Id>`
  → the file lands in that account's **Files** related list. Dedupe against the
  account's existing file titles first.
- **Auth:** OAuth 2.0 **JWT Bearer** flow (server-to-server, headless). No callback
  URL, no browser login, works from the LAN server / localhost — outbound HTTPS
  only. Signing reuses **PyJWT** (already a dependency), so no new backend deps.

## Environments

- **Build + test against the sandbox first:** `americaworks--itsupport.sandbox.my.salesforce.com`.
  Never develop against production client records.
- JWT audience + token endpoint for a sandbox: `https://test.salesforce.com`
  (production later uses `https://login.salesforce.com`).

---

# Part A — Generate the signing certificate (one-time)

The JWT is signed with a private key; Salesforce verifies it with the matching
public certificate you upload to the Connected App. **The `.key` is a secret** —
it never leaves the server and is gitignored. Only the `.crt` goes to Salesforce.

Run in Git Bash (openssl ships with Git for Windows). Put the files in a
gitignored `secrets/` folder next to `.env`:

```bash
mkdir -p secrets
# private key (KEEP SECRET)
openssl genrsa -out secrets/sf_jwt.key 2048
# self-signed public cert, valid 2 years (upload THIS to Salesforce)
openssl req -new -x509 -key secrets/sf_jwt.key -out secrets/sf_jwt.crt \
  -days 730 -subj "/CN=client-files-viewer-sf"
```

Add to `.gitignore` (if not already covered): `secrets/`

> Track the cert's expiry (730 days → renew before ~2028-09). When it expires,
> generate a new pair and re-upload the `.crt` to the Connected App.

---

# Part B — Create the External Client App

> Newer orgs (this sandbox included) have **retired the classic "New Connected App"
> button**. App Manager now shows only **New Lightning App** and **New External
> Client App**. Use **External Client App** — it supports the JWT Bearer flow, so
> the cert, Consumer Key, env vars, and smoke test are all unchanged.
>
> Exact label wording varies slightly by Salesforce release. Whatever the labels,
> the **four must-haves** are: (1) OAuth enabled, (2) the `.crt` uploaded for
> digital signatures, (3) the **JWT Bearer flow** enabled, (4) the integration
> user **pre-authorized**.

Do this **in the sandbox** (`americaworks--itsupport.sandbox.lightning.force.com`).

1. **Setup** → Quick Find → **App Manager** → **New External Client App**.
   - (If the button errors, first enable creation: Setup → Quick Find →
     **External Client App Settings** → allow creating External Client Apps / OAuth.)
2. **Basic Information:**
   - External Client App Name: `Client Files Viewer`
   - API Name: (auto) `Client_Files_Viewer`
   - Contact Email: your email.
   - Distribution State: **Local**.
   - **Create.**
3. Open the app → **Settings** tab → **OAuth Settings** → **Edit** (or "Enable OAuth"):
   - Check **Enable OAuth**.
   - **Callback URL:** `https://login.salesforce.com/services/oauth2/success`
     (unused by JWT, but required).
   - **Selected OAuth Scopes:** add **Manage user data via APIs (api)** and
     **Perform requests at any time (refresh_token, offline_access)**.
   - **Require Proof Key for Code Exchange (PKCE):** *unchecked*.
   - Under **Flow Enablement**, check **Enable JWT Bearer Flow** (wording may be
     "Issue JWT-based access tokens" / "JWT Bearer").
   - **Use digital signatures** → upload `secrets/sf_jwt.crt`. (If the cert upload
     isn't on this screen, it's under **Settings → OAuth Settings → Digital
     Signatures**.)
   - **Save.** Allow **2–10 minutes** to propagate before testing.

---

# Part C — Policies + pre-authorize the integration user

The JWT flow only works if the target user is **pre-authorized** for the app.
On the **Policies** tab this is split into two sections:

1. **App Policies → Select Profiles / Select Permission Sets** — this is the
   pre-authorization. Move the **integration user's Profile** (or a Permission Set
   they hold) from *Available* to *Selected* with the **►** arrow.
   - **Sandbox testing:** use your admin user → select the **System Administrator**
     profile. (A profile alone is enough; no permission set needed.)
   - **Production:** a dedicated integration user + a "SF API Integration"
     permission set (least privilege, attributable).
2. **OAuth Policies** (expand it): if a **Permitted Users** dropdown is present, set
   **Admin approved users are pre-authorized**; set **IP Relaxation → Relax IP
   restrictions** (so the LAN server's IP isn't blocked; tighten later if desired).
3. **Save.**

> The JWT `sub` must be that same user's **sandbox** username (`…@…com.itsupport`).

### Which user?

Pick the Salesforce user the JWT will act as (the `sub` claim). It needs:
**API Enabled**, read on **Assignment** (+ its Case Number field), and permission
to create **ContentVersion** and see **Accounts**.

- For sandbox testing you can start with your admin user.
- For production, use a dedicated **integration user** (ideally a Salesforce
  Integration-license user) so actions are attributable and least-privileged.
- **Important (sandbox usernames):** a sandbox appends the sandbox name to every
  username. Your `sub` must be the **full sandbox username**, e.g.
  `joe@americaworks.com.itsupport` (not the production `joe@americaworks.com`).

---

# Part D — Collect the values the app needs

1. Open the app → **Settings** tab → **OAuth Settings** → **Consumer Key and Secret**
   → **Reveal** (may prompt for an emailed verification code). Copy the
   **Consumer Key** (a long string starting `3MVG9…`). This is the JWT `iss`.
   *(The Consumer Secret is not needed for JWT.)*
2. Confirm the **integration username** (the `.itsupport` sandbox form).

---

# Part E — App configuration (env)

Add to `.env` (all gitignored). Values you gathered above:

```
SF_LOGIN_URL=https://test.salesforce.com        # sandbox; login.salesforce.com in prod
SF_CONSUMER_KEY=3MVG9...                          # Connected App Consumer Key (JWT iss)
SF_USERNAME=joe@americaworks.com.itsupport        # integration user (JWT sub)
SF_JWT_KEY_PATH=secrets/sf_jwt.key                # private key path
```

---

# Part F — Smoke test (proves auth end-to-end, read-only)

Once the app propagates, the first milestone is a **read-only** check: mint a JWT,
exchange it for an access token, then resolve a known case number to its Account
and list that account's existing files. No writes — safe against the sandbox.

The token exchange is a POST to `${SF_LOGIN_URL}/services/oauth2/token` with
`grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer` and the signed assertion
(claims: `iss`=consumer key, `sub`=username, `aud`=`${SF_LOGIN_URL}`, `exp`=now+3m,
RS256). I'll add `scripts/sf_smoke.py` for this step when we build.

### Gotchas
- **Propagation delay** — a fresh Connected App returns `invalid_client_id` for a
  few minutes. Wait and retry before debugging.
- **`invalid_grant: user hasn't approved this consumer`** — the `sub` user isn't
  pre-authorized (Part C) or the username is the prod form, not the `.itsupport`
  sandbox form.
- **`aud` mismatch** — if `https://test.salesforce.com` is rejected, try the My
  Domain URL `https://americaworks--itsupport.sandbox.my.salesforce.com` as `aud`.
- Treat the **Case Number as an opaque string** (leading zeros + trailing letter,
  e.g. `00039658168I`) — never cast to a number.

---

# Build milestones (after setup)

1. **Auth + resolve (read-only)** — JWT token, `Case Number → Account`, list files. `scripts/sf_smoke.py`.
2. **Upload** — `ContentVersion` push with dedupe against existing file titles.
3. **Mapping store** — persist name ↔ case number ↔ Account Id; auto-route repeat clients.
4. **UI** — a "Send to Salesforce" action in the file view (rep confirms case number first time, one-click after).

## Resolved schema (discovered via describe against the sandbox)
- Object: **`Assignment__c`**. Case Number is the record **`Name`** field.
  Client account lookup is **`Participant__c`** (→ Person Account). The other
  Account lookups (`Referral_Source__c`, `Site_Location__c`) are the org/worksite.
- Resolve path: `Assignment__c.Name = <case number>` → `Participant__c` → Account
  → upload a `ContentVersion` filed on that Account.

---

# Production security checklist (before cutover)

Rank: **🔴 must do before prod · 🟠 recommended · 🟡 operational.** Items marked
**✅ done in code** are already implemented in this repo; the rest are Salesforce
admin / deployment config. (Numbering matches the security review.)

## 1. 🔴 Dedicated least-privilege integration user
Sandbox authenticates as an **admin** (`jclark`). Production must use a dedicated,
least-privilege user so a stolen key can't do more than file assessments.

> **This recipe is proven** in the itsupport sandbox (2026-09-17) as user
> *Assessment Files Integration*. Full click-by-click prod runbook:
> **`docs/SALESFORCE_PROD_SETUP.md`**. Summary:

**Salesforce admin steps:**
1. **Create the user** — Setup → **Users → New User**:
   - **User License: standard `Salesforce`** — **not** *Salesforce Integration*.
     The Integration license is cheaper/API-only but **blocks `Read` on standard
     objects like Account** ("the user license doesn't allow the permission: Read
     Accounts"), so it can't do what we need.
   - **Profile: `Minimum Access - Salesforce`** (locked down; permissions come from
     the permission set below). With no password set + JWT-only auth, the user can't
     log in interactively despite the standard license.
   - **Role:** this org **requires** a Role — create/assign a dedicated **`Integration`**
     role (hierarchy position is irrelevant; visibility comes from View All below).
   - **Name / Username / Email:** *Assessment Files Integration*,
     `cfv-integration@americaworks.com` (username is global-unique; **prod has no
     sandbox suffix**, and a user *created directly in a sandbox* also keeps its
     plain username — only prod-copied users get `.itsupport`).
2. **Create a permission set** — Setup → **Permission Sets → New**,
   "CFV Salesforce Integration" (License **--None--**):
   - **System Permissions:** **API Enabled** (only).
   - **Object Settings:**
     - `Assignment__c` → **Read + View All Records + View All Fields**
     - `Account` → **Read + View All Records + View All Fields**
     - (**ContentVersion needs no explicit permission** — file create works with the
       standard license + Account read; verified.)
   - **No** Create/Edit/Delete on Assignment/Account, no **Modify All Records**, no
     "Modify All Data"/"View All Data".
   - **Manage Assignments → Add Assignment →** the integration user.
3. **Restrict where it can log in** (see #2) — set **Login IP Ranges** on the
   profile to the LAN server's public/egress IP.
4. **Authorize it for the External Client App** — app's **Policies → App Policies →
   Select Permission Sets** → add **CFV Salesforce Integration** (least-privilege:
   only this user has it). **Remove** the admin/System Administrator profile once the
   integration user works.
5. **Point the app at it** — in the production `.env`: `SF_USERNAME=<the integration
   user's username>`. The JWT `sub` and the app authorization (step 4) must be this
   same user. Restart the API and run `scripts/sf_smoke.py` — it should authenticate
   as the integration user.

## 2. 🔴 Lock down the External Client App
- Set **IP Relaxation** back to **"Enforce IP restrictions"** (we relaxed it for
  testing) and set the integration user's **Login IP Ranges** to the server IP.
- Keep **Permitted Users = "Admin approved users are pre-authorized."**

## 3. 🔴 Certificate + key hygiene
- The signing cert is 730 days — set a **renewal reminder** now; rotate by
  generating a new pair and re-uploading the `.crt`.
- Restrict the `secrets/sf_jwt.key` file ACL to the service account only.
- `secrets/` is gitignored — also exclude it from any **backup/imaging** that could
  copy it off-box.

## 4. 🟠 ✅ done in code — name-match safeguard
`POST /api/pdfs/{id}/salesforce` refuses to file when the PDF's client name doesn't
match the resolved account name, unless `override_name_mismatch: true` (the UI shows
a warning and the rep must click **"Send anyway"**). Blocked attempts audit
`SALESFORCE_PUSH` `outcome=denied`, `reason=name_mismatch`. Prevents mis-filing PII
onto the wrong record after a mistyped case number.

## 5. 🟠 ✅ done in code — no internal errors to clients
`_sf_client()` logs the real error to `cfv.salesforce` and returns a generic 503, so
config internals (env names, SF error bodies) aren't disclosed to callers.

## 6. 🟠 ✅ done in code — case-number input validation
`CaseNumberReq.case_number` is capped at 64 chars and `^[A-Za-z0-9._\-]+$`, on top of
the SOQL escaping (`sf.soql_str`) — belt-and-suspenders against injection.

## 7. 🟠 Minimize OAuth scopes
The External Client App currently has `api` + `refresh_token, offline_access`. JWT
bearer doesn't use refresh tokens — drop that scope, keep **`api`** only.

## 8. 🟠 Audit / rate-limit resolve + prefill
`/api/salesforce/{resolve,prefill}` let any `salesforce:read` user map a case number
to a client name + file list (enumeration). Consider emitting an audit event or
applying `auth/rate_limit.py` if that exposure matters.

## 9. 🟡 ✅ done in code — production guard on the write test script
`scripts/sf_upload_test.py` (which uploads) now refuses to run unless
`SF_LOGIN_URL` looks like a sandbox, or `--prod` is passed — so a prod `.env` can't
be used to push test data into real client records.

## 10. 🟡 Token caching (perf, not security)
`SFClient` mints a fresh JWT per instantiation (one token exchange per request).
Fine at rep volume; cache the access token with its expiry later if needed.
