# Multi-Location Access Control (Location Scoping)

**Status:** Spec / not started · **Owner:** Joe · **Drafted:** 2026-09-17

## Problem

The app was built for one site (Bronx). Other NYC offices are coming online —
**Bronx, 45th, Far Rockaway, Jamaica, 27th** — and each office's staff must see
**only their own office's** client assessment PDFs (and PC/scan/dashboard data).
A Bronx viewer must never see Far Rockaway's files, and vice-versa. Admins/HQ
still need an all-offices view.

## Core idea

Add a **`location`** dimension to two things and filter everything by it:

1. **Each PDF gets a location** — derived from the **lab PC it was scanned from**
   (each office owns its own lab PCs). This is the reliable, physical source of
   truth; "uploaded by Bronx staff" in practice means "came off a Bronx lab PC."
2. **Each user gets one or more locations** — derived from the **Office 365
   "Office" field** for SSO users (admin-assignable otherwise).
3. Every data read is filtered `WHERE location IN (the user's locations)`, unless
   the user holds a new **`location:all`** permission (admins / HQ).

Location is **orthogonal to role**: role = *what you can do*, location = *whose
data you see*. A "Bronx operator" and a "Jamaica operator" have the same abilities
over different data.

---

## Key decisions (with recommendations)

### D1. Deployment topology — **single instance + row scoping** (recommended)
Two ways to "keep offices separate":
- **A. One app instance, one DB, location-scoped rows (recommended).** Central
  admin, one Salesforce integration, a unified HQ view, and simple per-office
  filtering. **Requires the backend to reach every office's lab PCs** over the
  network (VPN/WAN or a routable LAN).
- **B. One deployment per office.** Separation is physical (separate DBs), but you
  lose the unified view, multiply the ops burden, and duplicate the Salesforce
  config per site.

**Decided: A** (confirmed 2026-09-17). The firewall will be opened so the backend
can reach every office's lab-PC subnet, so we run **one instance with location-
scoped rows** — central admin, one Salesforce config, unified HQ view.

### D2. PDF → location: **from the lab PC** (recommended)
Stamp `pdfs.location` at **scan time** from the PC's assigned location. Denormalized
onto the row so filtering is a plain indexed `WHERE` and a PC's PDFs are unambiguous.

### D3. User → location: **Office 365 "Office" field**, admin-overridable
- **SSO (Entra) users:** read the **Office** field (Graph `officeLocation`, aka AD
  `physicalDeliveryOfficeName`) on login → map to a location (see "Office field"
  below) → store on the user.
- **Local `/admin/login` users:** no O365 profile → **admin assigns** location(s).
- Admin can always override the auto-derived value.

### D4. Multi-location users — **support a set, not a single value**
Regional managers / floaters may cover several offices. Model location as a
**many-to-many** (`user_locations`), even though most users have exactly one.

### D5. Cross-location visibility — **`location:all` permission**
Admins (and an optional "HQ/regional" grant) get `location:all`, which bypasses the
filter and enables a **location picker** to view any office. Everyone else is hard-
scoped server-side.

---

## Data model / DB changes (Alembic)

```
locations
  id            smallserial pk
  code          text unique        -- 'bronx','45th','far_roc','jamaica','27th'
  name          text               -- 'Bronx', '45th Street', 'Far Rockaway', …
  active        boolean default true

location_office_aliases              -- maps messy O365 Office strings → a location
  office_value  text primary key     -- normalized lower/trimmed, e.g. 'far rockaway'
  location_id   smallint fk locations

pcs / pc_status
  + location_id smallint fk locations   -- which office owns this lab PC
  (also add `location` to the pcs.py static map)

pdfs
  + location_id smallint fk locations   -- denormalized from the PC at scan time
  index on (location_id, committed_at)  -- scoped list queries

user_locations                          -- many-to-many (D4)
  user_id      uuid fk users
  location_id  smallint fk locations
  primary_loc  boolean                   -- the user's home office
  pk (user_id, location_id)
```

Add a `location:all` permission to `auth/permissions.py` (admin; optionally a new
`hq`/`regional` role or a per-user grant).

---

## Logic changes

- **`scan.py`** — resolve each PC's `location_id` and pass it into `db.upsert` so
  every indexed/updated row carries the location. PC with no mapped location →
  land in an **"Unassigned" bucket** (visible only to `location:all`) rather than
  silently to everyone.
- **Auth (SSO) — `auth/sso.py`** — after id_token verification, obtain the user's
  Office (see below), map via `location_office_aliases`, and set/refresh their
  `user_locations`. Unmapped/empty Office → **no location granted** → they see
  nothing until an admin assigns one (fail-closed; surfaces as "contact IT").
- **Query scoping (the enforcement point)** — add the caller's allowed
  `location_id`s to every `pdfs`/`pc_status`/scan/dashboard query. Centralize in a
  helper (e.g. `auth/scope.py::location_filter(ctx)`) so no endpoint forgets it.
  `location:all` → no filter.
- **Salesforce push** — unchanged. Reps only ever see their office's files, so they
  can only push those; the target SF org/record is the same regardless of office.

## The Office 365 "Office" field — how to read it

The "Office" box in a user's O365 contact info is AD **`physicalDeliveryOfficeName`**,
surfaced in Microsoft Graph as **`officeLocation`**. Two ways to get it at login:

1. **Microsoft Graph (recommended, reliable):** in the SSO callback, use the
   access token to call `GET https://graph.microsoft.com/v1.0/me?$select=officeLocation`
   with the **`User.Read`** scope (basic, admin-consentable once). Works regardless
   of token config.
2. **Optional claim:** add `officeLocation`/`physicalDeliveryOfficeName` as an
   optional ID-token claim on the Entra app registration (Token configuration) so
   it arrives in the id_token — no extra Graph call, but depends on tenant config.

**Mapping:** the field is free text, so normalize (trim/lowercase) and match against
`location_office_aliases` (e.g. `"far rockaway"`, `"far roc"`, `"frr"` → `far_roc`).
Unmapped values get logged for an admin to add an alias — don't guess.

## UI

- **Location badge** in the topbar/sidebar showing the active office (and, for
  `location:all`, a **location picker** to switch/view "All offices").
- **Files / Dashboard / PCs / Logs** all reflect the active location automatically
  (server-scoped; the stat tiles, recent files, PC health, etc. become per-office).
- **Admin → Users:** show each user's **Location(s)**, auto-filled from Office for
  SSO users, editable; a filter by location. Flag users whose Office didn't map.
- **New Admin → Locations page** (perm `system:write`): CRUD locations, manage the
  Office→location aliases, and assign PCs to locations.
- Empty/unassigned states: a user with no location sees a clear "No office assigned —
  contact IT Support" rather than an empty app.

## Permissions / roles

- New **`location:all`** — granted to `admin` (bypass scoping + location picker).
- `operator` / `corporate_rep` / `viewer` stay as-is but are **location-scoped**.
- (Optional) a lightweight **`hq`/`regional`** grant for non-admins who need
  multiple offices without full admin.

## Rollout / migration

1. Ship schema + seed the 5 locations + Office aliases.
2. **Backfill:** all existing PDFs + PCs → **Bronx**; all existing users → Bronx
   (or by their Office). The app behaves exactly as today for Bronx on day one.
3. Add the other offices' PCs (with locations) to `pcs.py` once reachable (O1).
4. Turn on scoping; verify a Bronx viewer can't see another office's files.

## Edge cases / risks

- **Network reachability (O1)** — the biggest unknown; gates D1. If offices aren't
  reachable from one backend, this needs site-to-site connectivity or per-site
  deployments.
- **Unmapped Office / new hire** → fail-closed (no data) + admin alert, never
  fail-open to all offices.
- **PC with no location** → Unassigned bucket, admin-visible only.
- **A client seen at two offices** → two separate PDFs (one per office's PC),
  each scoped to its office. Fine.
- **Dedupe scope** — content dedupe currently spans all rows. Decide whether
  dedupe should be **per-location** (same client re-tested at two offices keeps
  both) — almost certainly yes; scope the dedupe query by location too.
- **Salesforce** — one SF org for all offices; location doesn't change the target,
  only who can initiate the push.

## Resolved (2026-09-17)

- **O1 ✅** Firewall will be opened so one backend reaches all office subnets →
  **single instance + row scoping** (D1 = A).
- **O2 ✅** Office→location aliases are **admin-managed in the UI** (Admin →
  Locations page), so no hard-coded strings — admins add/edit locations and their
  Office aliases as offices onboard.
- **O3 ✅** Some staff cover **multiple** offices → keep the `user_locations`
  many-to-many (D4).
- **O4 ✅** Content **dedupe is per-office** — the same client re-tested at two
  offices keeps a copy at each; scope the dedupe query by `location_id`.

## Still open

- **O5.** Any office that should be **local-accounts-only** (no SSO)? If so, those
  users get admin-assigned locations (D3 already covers this) — just confirm.

## Build milestones

1. **Schema + backfill** — locations, aliases, `location_id` on pcs/pdfs/user_locations; everything → Bronx.
2. **Scan stamps location** + per-location dedupe.
3. **Scoping helper + enforce on every read** (Files, dashboard, PCs, logs, archive).
4. **Office → location on SSO login** (Graph `officeLocation`) + admin override.
5. **UI** — location badge/picker, Admin → Locations page, Users location column/filter.
6. **Onboard offices** — add PCs, verify isolation, go live per office.
