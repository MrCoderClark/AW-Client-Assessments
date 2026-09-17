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

## Open questions for the SF admin
- **API names** needed for the SOQL: the Assignment object's API name, its **Case
  Number** field API name, and its **Account** lookup field. (Setup → Object
  Manager → Assignment → Fields, or I can pull them via the describe API once the
  smoke test connects.)
- Confirm the integration user's object/field permissions (Assignment read,
  ContentVersion create, Account read).
