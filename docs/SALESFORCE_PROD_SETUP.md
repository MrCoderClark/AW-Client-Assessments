# Salesforce Production Cutover — Runbook

Step-by-step to stand up the assessment-push integration in the **production**
PACE org. This is the *corrected* sequence proven end-to-end in the `itsupport`
sandbox on 2026-09-17 (all the dead-ends removed). Do these in order.

- **Concept + code:** `docs/SALESFORCE_INTEGRATION.md`
- **Names used here** (keep consistent):
  - External Client App: **Client Assessment Files Viewer**
  - Integration user: **Assessment Files Integration**
  - Role: **Integration**
  - Permission set: **CFV Salesforce Integration**

> Do everything below **in production** (`login.salesforce.com` / the prod My
> Domain). Production has its **own** External Client App, cert, user, and
> Consumer Key — nothing carries over from the sandbox except the app *code*.

---

## 0. Prerequisites
- A Salesforce admin in the prod org.
- One free **standard Salesforce** license seat for the integration user.
- Access to the LAN server's `.env` and a `secrets/` folder (gitignored).
- The server's outbound/egress public IP (for login-IP lockdown).

---

## 1. Generate a production signing cert + key (own, not the sandbox one)
On the LAN server, in Git Bash:
```bash
mkdir -p secrets
openssl genrsa -out secrets/sf_jwt_prod.key 2048
openssl req -new -x509 -key secrets/sf_jwt_prod.key -out secrets/sf_jwt_prod.crt \
  -days 730 -subj "/CN=client-files-viewer-sf-prod"
```
Keep `sf_jwt_prod.key` secret (gitignored, restricted ACL). Upload `sf_jwt_prod.crt`
to the app in step 2. Set a **renewal reminder** (~2028-09).

## 2. Create the External Client App — "Client Assessment Files Viewer"
Setup → **App Manager → New External Client App** (classic Connected Apps are
retired in this org).
- **Name:** `Client Assessment Files Viewer` · **Distribution State:** Local → **Create**
- Open it → **Settings → OAuth Settings → Edit / Enable OAuth**:
  - **Enable OAuth** ✓
  - **Callback URL:** `https://login.salesforce.com/services/oauth2/success` (unused by JWT, required)
  - **Scopes:** **Manage user data via APIs (api)** — *only* (drop `refresh_token`; JWT doesn't use it)
  - **Require PKCE:** unchecked
  - **Flow Enablement → Enable JWT Bearer Flow** ✓
  - **Use digital signatures** → upload `secrets/sf_jwt_prod.crt`
  - **Save**, then wait **2–10 min** to propagate.

## 3. Create the Integration role
Role is a **required** field on users in this org. Setup → **Roles → Set Up Roles**
→ under the top node **Add Role** → **Label:** `Integration` → **Save**.
(Hierarchy position doesn't matter — visibility comes from View All in step 5.)

## 4. Create the integration user — "Assessment Files Integration"
Setup → **Users → New User**:
- **First / Last:** `Assessment Files` / `Integration` (shows as **Created By** on every pushed file)
- **User License: `Salesforce`** (standard) — **NOT** *Salesforce Integration*
  (that license blocks `Read Accounts` and can't do the job)
- **Profile: `Minimum Access - Salesforce`**
- **Role: `Integration`** (from step 3)
- **Username / Email:** `cfv-integration@americaworks.com` (monitored inbox).
  In prod the username is exactly as typed — **no sandbox suffix**.
- **Save.** Do **not** set/keep a password — the user authenticates only via JWT,
  so it can't be logged into interactively.

## 5. Permission set — "CFV Salesforce Integration"
Setup → **Permission Sets → New** → Label `CFV Salesforce Integration`, License
**--None--** → Save. Then:
- **System Permissions → Edit:** **API Enabled** (only) → Save
- **Object Settings → Assignments (`Assignment__c`) → Edit:** **Read + View All
  Records + View All Fields** → Save
- **Object Settings → Accounts → Edit:** **Read + View All Records + View All
  Fields** → Save
- *(No ContentVersion/"Content Versions" entry is needed — file create works with
  the standard license + Account read. Verified in sandbox.)*
- **No** Create/Edit/Delete, no **Modify All Records**, no View/Modify All Data.
- **Manage Assignments → Add Assignment →** **Assessment Files Integration** → Assign.

## 6. Authorize the user on the app + lock it down
On **Client Assessment Files Viewer → Policies**:
- **App Policies → Select Permission Sets** → move **CFV Salesforce Integration**
  into Selected → Save. (Least-privilege: only this user holds that set. Do **not**
  authorize by a broad profile or leave any admin authorized.)
- **OAuth Policies:** **Permitted Users = Admin approved users are pre-authorized**;
  **IP Relaxation = Enforce IP restrictions**.
- On the integration user's profile (**Minimum Access - Salesforce**) set **Login IP
  Ranges** to the LAN server's egress IP.
- Wait **1–2 min** for policy propagation.

## 7. Get the Consumer Key
App → **Settings → OAuth Settings → Consumer Key and Secret → Reveal** → copy the
**Consumer Key** (starts `3MVG9…`). The secret is not needed for JWT.

## 8. Configure the production `.env`
```
SF_LOGIN_URL=https://login.salesforce.com        # prod (was test.salesforce.com in sandbox)
SF_CONSUMER_KEY=3MVG9...                          # the prod app's Consumer Key
SF_USERNAME=cfv-integration@americaworks.com      # prod integration username (no suffix)
SF_JWT_KEY_PATH=secrets/sf_jwt_prod.key           # the prod private key
```
> `.env` must be UTF-8 **without a BOM** (see DEPLOYMENT.md — uv drops everything
> after a BOM). If token exchange later complains about audience, set
> `SF_JWT_AUDIENCE=https://<prod-mydomain>.my.salesforce.com`.

## 9. Restart the API and verify
```powershell
# restart however prod runs it (service Restart-Service -Force, or re-run uvicorn --env-file .env)
```
Then, from the server:
```bash
uv run --env-file .env python scripts/sf_smoke.py           # auth as the integration user (read-only)
uv run --env-file .env python scripts/sf_resolve.py <case#>  # a real case number → account + files (read-only)
```
- Expect `✅ … connection is GOOD` and `Authenticated as: Assessment Files Integration`.
- **First real upload:** do it through the **app UI** (a rep pushing one assessment),
  then confirm the file on that client's Salesforce record. Do **not** run
  `sf_upload_test.py` against prod — it self-refuses unless `--prod`, and it writes.

---

## Security hardening (must-do at cutover — mirrors the review)
- 🔴 **Least-privilege user** — steps 4–5 above (standard license, Minimum Access, one permission set).
- 🔴 **IP enforcement** — step 6 (Enforce + Login IP Ranges to the server).
- 🔴 **Key hygiene** — dedicated prod key, restricted ACL, gitignored + excluded from backups, renewal reminder.
- 🟠 **`api` scope only** — step 2 (no refresh_token).
- 🟠 Name-match safeguard, generic error messages, input validation, write-script prod guard — **already in the code**.
- 🟠 Consider auditing/rate-limiting `resolve`/`prefill` (enumeration surface).

## Gotchas we hit in the sandbox (so prod goes clean)
- **Don't use the Salesforce Integration license** → "the user license doesn't allow the permission: Read Accounts." Use a standard Salesforce license.
- **Role is required** in this org → create the `Integration` role first, or user save fails.
- **Sandbox usernames:** users *copied from prod* get a `.sandboxname` suffix; a user *created directly in a sandbox* keeps its plain username. **Prod usernames have no suffix.**
- **`invalid_grant: authentication failure`** = wrong/inactive `sub` username. **`invalid_app_access`** = the user isn't authorized on the app (step 6). **`invalid_client_id`** = app still propagating (wait).
- **ContentVersion** needs no explicit object permission.
- Propagation: new app 2–10 min; policy changes 1–2 min.

## Rollback / disable
- **Fastest kill switch:** deactivate the **Assessment Files Integration** user (Setup → Users → uncheck Active) — all pushes stop immediately.
- Or remove **CFV Salesforce Integration** from the app's App Policies, or disable OAuth on the app.
- App-side: nothing is deleted from Salesforce by disabling; already-filed files remain on client records.
