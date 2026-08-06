# Archiving + Restore

**Status:** ✅ shipped 2026-08-05 · **Owner:** Joe · **Created:** 2026-08-04 · **Last updated:** 2026-08-05

Move old committed PDFs out of active date folders while keeping them
browsable, viewable, and restorable in one click. First real target:
everything committed before 2026-01-01 (all 2024 + 2025 files).

---

## What actually shipped (2026-08-05)

Backend, all live:

- **Migration** `b2c3d4e5f6a1_pdfs_archive_columns.py` — `archived_at`, `archive_path`, `archive_status` + partial index `ix_pdfs_archived_at`.
- **`share.py`** — `list_active_date_folders(share)` (skips `_Archive/`), `smb_rename_with_retry(src, dst)` (credit-exhaustion backoff). `backfill_share.py` refactored to use the helper.
- **`archive.py`** — service module. `archive_ids`, `restore_ids`, `archive_by_date_stream`, `restore_by_date_stream`. Keyset-paginated (100/batch — reduced from 500 for progress-frame frequency), 4-worker `ThreadPoolExecutor`, per-row committed tx, in-memory job registry with cancel + list. Auto-pause after 5 consecutive failures.
- **`repair.py`** + `scripts/repair_archive_state.py` — six state classifications, four auto-fixable (`stale_active_row`, `stale_archive_row`, `lost`, `both_exist`), two reported-only (`active_missing`, `active_stale_archive_copy`).
- **`auth/audit.py::emit_sync`** — sync psycopg audit writer sharing the async hash chain via `pg_advisory_xact_lock`. Per-file `PDF_ARCHIVED` / `PDF_RESTORED` + bulk `PDF_BULK_ARCHIVE` / `PDF_BULK_RESTORE` + `PDF_ARCHIVE_REPAIRED` + `PDF_ARCHIVE_LOST`.
- **API endpoints** on `api.py` (all gated on `pdf:archive` for admin+operator): `POST /api/pdfs/bulk/archive`, `POST /api/pdfs/bulk/restore`, `POST /api/pdfs/archive-search`, `POST /api/pdfs/archive-preview`, `POST /api/pdfs/restore-preview`, `POST /api/pdfs/archive-by-date` (SSE), `POST /api/pdfs/restore-by-date` (SSE), `POST /api/pdfs/archive-jobs/{id}/cancel`, `GET /api/pdfs/archive-jobs`, `POST /api/pdfs/repair-check`, `POST /api/pdfs/repair-apply`.
- **Guards**: `commit.py` dedupe SQL adds `AND archived_at IS NULL`. `GET /api/pdfs` gains `?archived=false|true|all` (default false) and requires `pdf:archive` for anything other than `false`. `GET /api/pdfs/{id}/content` returns 404 (not 403 — doesn't leak existence) for viewers hitting an archived row. `PATCH /api/pdfs/{id}` 409s on archived rows. `/api/pcs` + `/api/logs` COUNT queries filter `archived_at IS NULL` so archived rows never contribute to surface counts (Dashboard "Total Files", PC file counts).

Frontend, all live:

- **AppProvider** — always fetches the active slice (`?archived=false`); `mutationVersion` counter increments on each archive/restore so components with their own lists can re-fetch.
- **Files page** — owns its own local `archivedView` state + fetches its own list when in Archived/All view. Chip row (Active / Archived / All) hidden entirely for viewers. Row Status pill shows "Archived" when appropriate. Bulk toolbar is context-sensitive: Archive/Restore/Commit/Delete buttons appear based on selection + current view.
- **PDF drawer** — Archived banner (surfaces `archive_status='lost'` when relevant) + inline Restore button replaces Edit for archived rows.
- **`/admin/archive` page** — five stacked cards: (1) Search & restore with live debounced multi-field search + per-row and bulk restore; (2) Bulk archive by date (preset dropdown + custom range + live preview count + confirmation modal + live progress bar + Cancel + error tail); (3) Bulk restore by date (mirror, with committed_at / archived_at toggle); (4) Running/recent operations (polls `/archive-jobs` every 3s); (5) Repair panel (dry-run scan → apply flow with per-kind counts + detail list).
- **Sidebar** — new Archive nav entry (`IconArchive`), gated on `pdf:archive`. Admin layout no longer gates the whole `/admin/*` route group on `user:read` — each admin page uses its own `RequirePerm` so operators can reach `/admin/archive` without `user:read`.

Deviations / additions vs the original plan:

- Batch size lowered from 500 → 100 (progress frames tick every ~2s instead of every ~12s).
- SSE responses ship with `X-Accel-Buffering: no` + `Cache-Control: no-cache, no-transform` and a 2KB comment pad to force flush past small proxy buffers.
- `archive-by-date` and `restore-by-date` **spawn a daemon thread** that consumes the generator and pushes frames into an in-memory `queue.Queue`; the SSE response just drains the queue. **Client disconnect no longer halts the archive** — the work runs to completion even if the browser closes.
- `GET /api/pdfs/archive-jobs` was added (not in the original endpoint table) to power the "Running / recent operations" panel.
- Aggregate-count filtering on `/api/pcs` and `/api/logs` was added after ship — the plan's D4 said "hidden from every frontend surface" but I initially only filtered `list_pdfs`.
- Viewer permission gate on `?archived=true|all` was added after ship — same reason. Viewers can no longer see archived rows anywhere.

Known nice-to-haves (deferred, not tracked as bugs):

- Reconnect-to-running-job: the archive panel doesn't reattach to a live SSE for a job started in another tab/session. `/admin/archive` still shows the job in the Running/recent table with poll-based progress; that's the fallback until someone builds proper reattach.
- Progress persistence across backend restarts: the in-memory job registry (`_JOBS`) dies with the process. A restart mid-archive orphans the in-flight batch (repair panel catches the resulting inconsistency).
- Legal-hold flag, per-user retention policies, automated scheduled archival — all out of scope, as documented in "Not in scope for v1" below.

Round-trip verified on real files: `scripts/archive_smoke.py`. First real bulk archive was "everything before 2024" (91 files) on 2026-08-05.

---

## Decisions (locked)

| # | Decision | Value | Rationale |
|---|---|---|---|
| D1 | Where do archived files live? | **A — same share, `\\192.168.70.10\Client_Assessments\_Archive\MM-DD-YYYY\`** | No storage-admin work; rename ops are fast and reversible. |
| D2 | "before 2026" definition | **`committed_at < 2026-01-01`** in America/New_York | All 2024 + 2025 files; anything from 2026-01-01 onward stays live. |
| D3 | Space savings needed? | No | Not cited as a driver. Revisit only if D1 changes. |
| D4 | Must the `_Archive` folder itself be invisible in the app? | **Yes — hidden from every frontend surface** | See "Hidden-from-UI guarantees" section below. |
| D5 | UI selection interface | **Date picker + year quick-pick + custom range** | User asked for date/year selection explicitly. |
| D6 | Must-not-crash targets | **≥ 50,000 files in a single bulk op** without OOM, DB deadlock, or HTTP timeout | See "Scale + robustness" section below. |

---

## What "archived" means

- **File on disk:** renamed (moved) from `.../MM-DD-YYYY/file.pdf` → `.../_Archive/MM-DD-YYYY/file.pdf`. Same share (D1 default). Atomic per file, reversible in ms.
- **DB row:** stays in `pdfs`; two new columns record archival state.
- **App behavior:**
  - Hidden from default Files list; visible under an "Archived" filter chip.
  - Still viewable in the PDF drawer — loads bytes from `archive_path`.
  - Excluded from `commit.py` dedupe SQL so a re-scanned copy of an archived file isn't silently dropped as a duplicate.

---

## Hidden-from-UI guarantees (D4)

The `_Archive/` subfolder and every path underneath it must not appear anywhere in the app. Checklist:

- **`api.py::list_pdfs`** — filters `WHERE archived_at IS NULL` unless caller passes `?archived=true|all`. Default view excludes archived rows.
- **`api.py::pdf_content`** — accepts an archived row's id and serves from `archive_path`; but the archived row only reaches this handler if the caller already opted in via the filter above.
- **PC file-explorer** (`/api/pc/browse` etc.) — walks lab PCs, not the destination share, so `_Archive/` is already unreachable from this surface.
- **Scheduled scan** (`scan.py`) — walks lab PCs only; never touches the destination share.
- **Commit writer** (`commit.py`) — writes into `MM-DD-YYYY/` folders it creates; never lists or reads from `_Archive/`.
- **`scripts/backfill_share.py`** — its folder walker filters top-level entries with `DATED_FOLDER = re.compile(r"^\d{2}-\d{2}-\d{4}$")`. `_Archive` doesn't match → already excluded. Adding an explicit belt-and-suspenders skip in Branch 34's PR for future-proofing.
- **Notifications** — archive-emitted notifications include a link to `/admin/archive` (admin-only), not to the archived files themselves.
- **Command palette / search** — pulls from the same `list_pdfs` surface; archived items don't appear.

Regression watch: any new endpoint that reads from the share needs to skip `_Archive/` (a shared helper `_list_active_date_folders(share)` will centralize that filter — Branch 34 introduces it).

---

## Scale + robustness (D6)

Target: handle a single bulk-archive operation covering **50,000+ files** without crashing the backend, database, or UI.

### Backend — bulk-by-date runs as a streaming job, not a single request

`POST /api/pdfs/archive-by-date` returns a **`text/event-stream`** response (same SSE pattern used by `/api/scans` and `/api/commits`). The generator:

1. Runs a single `SELECT id FROM pdfs WHERE committed_at < :cutoff AND archived_at IS NULL ORDER BY id` and paginates in batches of 500 via `WHERE id > :last_id` (keyset pagination — no OFFSET, no large in-memory list).
2. For each batch, uses a `ThreadPoolExecutor` (default 4 workers, `--parallel` env override) to run the SMB rename in parallel. **Retry on credit exhaustion** — same helper we added to `backfill_share.py`.
3. After each file's rename succeeds, the row is updated in its own tiny committed transaction (`UPDATE pdfs SET archived_at = now(), archive_path = %s WHERE id = %s`). No long-held row locks.
4. Yields one SSE frame per batch with `{done: N, total: T, batch_ms: X, errors: [...]}`.
5. If the client disconnects, the generator keeps running to completion via a `finally`-style guard (same trick as the scan/commit notify wrapper).

### Database — no scary transactions

- Enumeration: keyset paginated, streams rows to the worker pool without holding a snapshot on the whole table.
- Updates: one row per commit. Never `UPDATE ... WHERE committed_at < :cutoff` in a single statement (that would take an ACCESS EXCLUSIVE ... actually, `UPDATE` takes `ROW EXCLUSIVE`, but a 50k-row single UPDATE stalls concurrent readers on those rows for the duration).
- Index-supported: `ix_pdfs_archived_at` (partial, `WHERE archived_at IS NOT NULL`) + existing `pdfs.committed_at` if present, otherwise we add it.
- Idempotent: `WHERE archived_at IS NULL` in the per-row UPDATE guarantees rerunning after a partial failure is safe — already-archived rows are skipped.

### Frontend — never renders 50k rows

- Preview is a **count**, not a list. `POST /api/pdfs/archive-preview` returns `{ count, sample: [10 filenames] }` — enough to sanity-check, not enough to bog the browser.
- Progress panel is a single SSE consumer showing `done / total`, current rate, ETA, and a tail of the last ~20 errors. No client-side accumulation of per-file records.
- Files-page listing is already client-paginated at 25/50/100. Archived list uses the same pagination via `?archived=true&limit=100&offset=...`.

### Guardrails

- **Confirmation modal** before firing a bulk-by-date archive: shows `"You are about to archive N files committed before D. This cannot be undone in a single click — restore is per-file or per-batch. Continue?"`
- **Per-user rate limit** on `archive-by-date` — 1 concurrent job per admin (409 if another is already running).
- **Cancel button** on the progress panel — sets a `cancelled_at` flag on an in-memory job registry; the generator checks it between batches and stops cleanly.
- **Automatic pause** if 5 consecutive SMB renames fail — surfaces "share unreachable, paused, retry?" in the UI instead of grinding through 50k errors.

---

## Recovery (restore + repair)

Restore is a first-class flow, not an afterthought — same rigor as archive.

### Four restore paths

| Path | Trigger | Scope |
|---|---|---|
| **Search & restore** (primary "client request" flow) | `/admin/archive` search panel — any combination of first name, last name, filename, date range, assessment type. At least ONE field required. | 1 or more rows matching the query |
| **Single-file restore** | "Restore" button in the PDF drawer | One row |
| **Bulk restore** | Selected rows in the Files page Archived view + toolbar button | 1–N rows chosen by the user |
| **Bulk restore by date** | `/admin/archive` panel — mirror of the archive form | Every archived row where `archived_at` (or `committed_at`) falls in the picked range |

All four share the same underlying service function `restore_ids(conn, ids: list[int])` and, for streaming variants, the same SSE generator scaffolding as archive.

### Search & restore — the primary admin workflow

Real-world scenario: an admin gets a request like _"can you pull the O*NET report for Jane Doe from last spring?"_ The archive UI needs to make this a 15-second job, not a 15-minute one. Search must work on any single field:

- **First name only** — matches any archived row where `first_name ILIKE '%jane%'`.
- **Last name only** — matches `last_name ILIKE '%doe%'`.
- **First AND last** — both filters ANDed.
- **Date or date range** — filters on `committed_at` (default) with an optional toggle for `archived_at`.
- **Assessment type** — narrow to O_NET, VIA, etc.
- **Filename fragment** — matches either `filename` or `proposed_name` for cases where the requester quotes a partial name.

**All fields optional, but at least ONE required** (server returns 400 with a clear message if the body has no criteria — prevents "restore everything" via an empty query).

Query targets only `archived_at IS NOT NULL AND archive_status = 'archived'` rows — searching active files goes through the existing Files page. Results are capped (default 50, max 500) with keyset pagination.

Ponytail on the SQL: start with `ILIKE '%foo%'` on first_name / last_name. If matching starts to feel slow (hundreds of thousands of rows), add a pg_trgm index — one migration, no code change. Not worth building preemptively.

### Per-row restore logic

```
1. SELECT id, dest_path, archive_path, archived_at
     FROM pdfs WHERE id = %s AND archived_at IS NOT NULL FOR UPDATE
   - If row is missing or already-restored → 409, no-op.
2. Confirm file exists at archive_path (smbclient.stat).
   - If missing → row marked with status="archive_file_missing" for the repair
     script; user sees a clear error, no fabricated success.
3. Ensure target directory exists at dest_path's parent (make it if not — the
   original dated folder may still be there, but be defensive).
4. smbclient.rename(archive_path -> dest_path).
   - If the target already has a file at dest_path (shouldn't, but…), append
     a `.restored-YYYYMMDD-HHMMSS` suffix and record the actual path used.
5. UPDATE pdfs SET archived_at = NULL, archive_path = NULL,
                   dest_path = <actual path used>,
                   updated_at = now()
     WHERE id = %s;
6. Emit PDF_RESTORED audit + scan_commit notification.
```

Each row's operation is its own committed transaction — no long locks.

### Retry safety (both directions)

Every archive and restore operation is designed to be **idempotent on rerun**:

- Archive worker skips rows where `archived_at IS NOT NULL` — a partially completed archive job can be resumed by hitting the same endpoint again.
- Restore worker skips rows where `archived_at IS NULL` — same story.
- The rename op itself is atomic on the SMB side. Post-rename DB update might fail (network blip); the repair script (below) reconciles.

### Recovery from partial failure

Three states can be created by a mid-op crash. Each is detectable and fixable:

| Symptom | State | Fix |
|---|---|---|
| Row shows `archived_at IS NOT NULL` but file at `archive_path` is missing | Archive DB update landed, but the file was later deleted externally (or move never finished cleanly) | User sees clear error on restore attempt. Admin can clear the row via a "Mark as lost" button (writes `archive_path = NULL, archived_at = 'lost'::text` — actually a status column, see below) |
| Row shows `archived_at IS NOT NULL` but file is still at `dest_path` (not at `archive_path`) | Rename succeeded, DB update failed | Repair script detects (stat both paths), completes the DB update |
| Row shows `archived_at IS NULL` but file only exists at `archive_path` | Restore DB update failed after rename back | Repair script detects, either completes the DB update (if file is at dest) or reverses the rename (if user is mid-restore-cancel) |

**Additional column for status clarity** — the schema gets one extra nullable text:

```sql
ALTER TABLE pdfs
  ADD COLUMN archive_status TEXT;  -- 'archived' | 'lost' | NULL
```

`archived_at IS NOT NULL AND archive_status = 'archived'` is the normal case. `archive_status = 'lost'` is a tombstone for "file was here, is gone, DB row preserved for history".

### Repair script — `scripts/repair_archive_state.py`

Walks `pdfs` and reconciles. Modes:

- `--dry-run` (default) — prints what would change, exits.
- `--fix` — applies fixes.
- `--only-check id,id,id` — target specific rows.

Checks per row:

1. If `archived_at IS NULL` and `archive_path IS NULL` → active state, verify file exists at `dest_path`. If missing, mark row for admin review (do not delete row).
2. If `archived_at IS NOT NULL` and `archive_status = 'archived'` → verify file exists at `archive_path`. If missing but exists at `dest_path` → row is stale; roll DB back to active state. If missing everywhere → set `archive_status = 'lost'`.
3. If archive_path and dest_path both exist → DB update failed post-rename; complete the update to whichever state matches (prefer archived if `archived_at` is set).

Uses the same shared helper `_list_active_date_folders(share)` so it never walks `_Archive/` when checking active-state files.

### Notification + audit

Every state transition emits an audit row and a notification:

- `PDF_ARCHIVED` / `PDF_RESTORED` — per-file
- `PDF_BULK_ARCHIVE` / `PDF_BULK_RESTORE` — one per bulk op with `context: {count, before?, after?, ids?, cancelled_at?}`
- `PDF_ARCHIVE_REPAIRED` — emitted by the repair script when it reconciles state (audit only, no notification)
- `PDF_ARCHIVE_LOST` — when a file that should be at `archive_path` is missing (SEC-severity notification; admin sees it in the bell dropdown)

---

## UI: date/year picker (D5)

New page `/admin/archive`. Five panels, top to bottom:

**1. Search & restore** (primary use case — top of page)

```
┌ Find archived file ──────────────────────────────────────┐
│                                                          │
│  First name    [ jane            ]                       │
│  Last name     [ doe             ]                       │
│  Filename      [                 ]  (partial ok)         │
│  Assessment    [ Any             ▼]                      │
│  Date          [ 2025-01-01 📅 ] to [ 2025-12-31 📅 ]    │
│                Filter on: (●) Committed  (○) Archived    │
│                                                          │
│  ┌──────────────────────────────────────────────────┐   │
│  │ 3 matching archived file(s)                       │   │
│  ├──────────────────────────────────────────────────┤   │
│  │ ☐ O_NET_Interest_Profiler-Jane_Doe.pdf           │   │
│  │   Committed 2025-04-12 · Archived 2026-08-05     │   │
│  │   O*NET Interest Profiler          [ Restore ]   │   │
│  │                                                    │   │
│  │ ☐ VIA_Character_Strengths_Profile-Jane_Doe.pdf   │   │
│  │   Committed 2025-04-12 · Archived 2026-08-05     │   │
│  │   VIA Character Strengths          [ Restore ]   │   │
│  │                                                    │   │
│  │ ☐ StrengthsProfile-Jane_Doe.pdf                  │   │
│  │   Committed 2025-09-03 · Archived 2026-08-05     │   │
│  │   StrengthsProfile                 [ Restore ]   │   │
│  └──────────────────────────────────────────────────┘   │
│                                                          │
│  [Select all]         [ Restore selected (0) ]           │
└──────────────────────────────────────────────────────────┘
```

- All fields optional; **at least one required** (Restore button disabled otherwise).
- Live search on any field change, debounced 400ms.
- Empty result state shows a clear "No archived files match your search" with a hint about what fields to try.
- Each row's **Restore** button fires a single-file restore in-place (no page navigation) and shows a small "Restored" toast when done. Row disappears from the search results after restore (it's no longer archived).
- Bulk **Restore selected** works when ≥ 1 checkbox is ticked; runs through the same SSE progress panel as bulk-by-date.

**2. Bulk archive by date range**

```
┌ Archive files ───────────────────────────────────────────┐
│                                                          │
│  Preset:  [ Everything before 2026 ▼ ]                   │
│           - Everything before 2026 (default)             │
│           - Everything before 2025                       │
│           - Everything before 2024                       │
│           - Older than 90 days                           │
│           - Older than 1 year                            │
│           - Custom range…                                │
│                                                          │
│  Cutoff:  [ 2026-01-01 📅 ]  (auto-fills from preset)    │
│                                                          │
│  → 12,847 files eligible                                 │
│    Sample: O_NET_Interest_Profiler-A_B.pdf, …            │
│                                                          │
│                                       [ Archive files ]  │
└──────────────────────────────────────────────────────────┘
```

- Preset dropdown updates the date input.
- "Custom range…" reveals a two-input `[From] → [To]` date range.
- Preview count refreshes on cutoff change (debounced 400ms).
- **Archive files** button opens a confirmation modal, then hits the SSE endpoint.

**3. Bulk restore by date range**

Mirror of the archive panel. Same preset dropdown ("Everything archived in 2024", "Everything archived in the past 90 days", "Custom range…"), same live preview count, same confirmation modal. Progress panel shows `done / total`, rate, ETA, last errors.

The "before" cutoff on the restore form operates on `archived_at` by default; a small toggle switches it to `committed_at` for the case of "restore everything I archived that was originally from 2025".

**4. Running / recent operations**

Table of last 50 archive/restore ops (from audit), with columns: When · Actor · Kind (archive / restore / repair) · Count · Range · Status (running / complete / failed / cancelled). Currently-running row has a live progress bar tied to the SSE stream and a **Cancel** button.

**5. Repair panel** (collapsed by default)

Small "Run repair scan" button that invokes the repair script's dry-run path via `POST /api/pdfs/repair-check`. Shows counts of mismatches by type. Applying fixes requires an explicit **Apply repairs** button (admin `pdf:archive` permission).



New Alembic migration (planned rev, TBD id):

```sql
ALTER TABLE pdfs
  ADD COLUMN archived_at    TIMESTAMPTZ,
  ADD COLUMN archive_path   TEXT,
  ADD COLUMN archive_status TEXT;  -- 'archived' | 'lost' | NULL

CREATE INDEX ix_pdfs_archived_at
  ON pdfs (archived_at) WHERE archived_at IS NOT NULL;
```

No new table. Three nullable columns; existing rows unaffected. `archive_status = 'lost'` is a tombstone for rows whose archive file is confirmed missing (see Recovery section).

---

## Backend

### API surface (all gated by new `pdf:archive` permission)

| Method + Path | Body | Behavior |
|---|---|---|
| `POST /api/pdfs/bulk/archive` | `{ ids: [1,2,3] }` | Rename each file to archive location, set `archived_at`+`archive_path`, emit `PDF_ARCHIVED` audit + `scan_commit` notification with counts. |
| `POST /api/pdfs/bulk/restore` | `{ ids: [1,2,3] }` | Reverse: rename back, clear the two columns, emit `PDF_RESTORED`. |
| `POST /api/pdfs/archive-search` | `{ first_name?, last_name?, filename?, before?, after?, date_field?: 'committed_at'\|'archived_at', assessment_type?, limit?, offset? }` | Search archived rows. **At least one criterion required** — empty body returns 400. Returns `{ results: [...], total, limit, offset }`. Powers the search-and-restore panel. |
| `POST /api/pdfs/archive-preview` | `{ before, after? }` | Returns `{ count, sample: [≤10 filenames] }` — cheap sanity-check used by the date picker. |
| `POST /api/pdfs/restore-preview` | `{ before, after?, ids? }` | Same shape for the restore flow. `ids` overrides the date range for the "restore this whole batch" button. |
| `POST /api/pdfs/archive-by-date` | `{ before, after? }` | Streams SSE progress. Keyset-paginated in 500-row batches with 4 parallel SMB workers + credit retry. See "Scale + robustness". |
| `POST /api/pdfs/restore-by-date` | `{ before, after? }` | Symmetrical streaming restore for a date-range slice of archived rows. |
| `POST /api/pdfs/archive-jobs/{id}/cancel` | – | Sets the in-memory cancel flag on a running job (archive OR restore). Idempotent. |

New permission `pdf:archive` added to the `admin` and `operator` role sets in `auth/permissions.py`.

### Guards to add to existing code

- `commit.py` dedupe SQL — `WHERE md5 = %s AND archived_at IS NULL`. Without this, a re-appearing file that matches an archived md5 would be treated as a dup and its source silently deleted.
- `api.py::pdf_content` — if row has `archive_path`, serve from that path instead of `dest_path`.
- `api.py::list_pdfs` — add `?archived=false|true|all` query param, default `false`.

### Failure handling

| Failure | State | Recovery |
|---|---|---|
| File move fails | Row unchanged; error surfaced to caller | Retry the same operation |
| Move succeeds, DB update fails | File at archive path; row still points at old `dest_path` | `scripts/repair_archive_state.py` walker checks file existence at each row's claimed path and reconciles |

Full 2PC not worth the complexity at this scale — a repair script covers the rare mismatch case.

---

## Frontend

### Files page (`/dashboard/files`)

- Chip row grows to four: **Active** (default) · **Archived** · **All** · (leave room for future).
- Row pill on archived rows: gray "Archived" tag.
- Bulk-select toolbar gets an **Archive** button (or **Restore** button when viewing Archived).

### PDF drawer

- If row has `archived_at`, show a top banner: "This file is archived" with a **Restore** button (admin/operator only). Viewer still loads and displays the PDF.

### New page: `/admin/archive`

Admin-only. Two sections:
1. **Archive by date** — date picker (default 2026-01-01), preview count via `POST /api/pdfs/archive-preview`, then a **Confirm** button that hits `/archive-by-date` and shows SSE progress.
2. **Recent operations** — last 50 archive/restore rows from audit, with counts.

Sidebar entry gated on `pdf:archive`.

---

## Notifications + audit

New audit actions:
- `PDF_ARCHIVED` — one per file, `context: {archive_path, prior_dest_path}`
- `PDF_RESTORED` — one per file, `context: {restored_from, restored_to}`
- `PDF_BULK_ARCHIVE` — one per bulk op, `context: {count, before_date?, requested_ids?}`

Notification category: `scan_commit` (everyone sees ops-flavor events). Title example: `"127 PDFs archived (pre-2026)"`.

---

## Risks

| Risk | Mitigation |
|---|---|
| Concurrent scan+commit lands a file that matches an archived md5 | Dedupe SQL excludes archived rows; new file lands in current-day folder as normal |
| Frontend still shows archived files everywhere | `list_pdfs` default excludes; chip toggles let you see them |
| User archives something and immediately wants it back | Rename op reverses in ms |
| Massive bulk archive locks the UI | Runs via SSE-streaming generator (same pattern as scan/commit) |
| Same-share archive doesn't reclaim disk | Called out in D3 — only relevant if disk pressure is the actual driver |
| A running scan/commit trips over an in-flight archive rename | Both operations are per-file; conflict window is small. First operation wins; second gets a normal "file not found" and continues. |

---

## Rollout — branches

Small, independently shippable. Backfill can run in parallel — none of these touch the ingest path.

| # | Branch | Deliverable |
|---|---|---|
| 34 | `archive-schema-and-service` | Migration (3 columns + index); shared helper `_list_active_date_folders(share)`; service functions `archive_ids`, `restore_ids`, streaming `archive_by_date_stream`, `restore_by_date_stream`; new audit action codes and notification kinds; smoke test covering both directions + idempotency. |
| 35 | `archive-api-and-dedupe-guards` | New endpoints (bulk + streaming + preview + cancel for both archive AND restore, plus `archive-search`), `pdf:archive` permission wired, dedupe + list + content guards. |
| 36 | `archive-ui-files-page` | Files-page chip row addition (Active / Archived / All), row pill on archived rows, bulk archive/restore button on the toolbar. |
| 37 | `archive-admin-page` | `/admin/archive` — search & restore panel (top, primary flow), bulk archive panel, bulk restore panel, running/recent ops table with live SSE progress, repair panel. |
| 38 | `archive-drawer-banner` | Banner + restore button in PDF drawer for single-file recovery. |
| 39 | `archive-repair-script` | `scripts/repair_archive_state.py` (`--dry-run` / `--fix` / `--only-check`) + `POST /api/pdfs/repair-check` and `/repair-apply` endpoints wired to the admin page repair panel. |

---

## Not in scope for v1

- Compressing archived files (tar/zip per month or year). Add if disk becomes an issue.
- Automatic archival on a schedule (e.g. "archive everything older than N days at 3 AM Sunday"). Manual trigger for M1.
- Per-user archive retention policies.
- Legal-hold flag (prevent archival of specific rows).

---

## How to pick this up next session

**All six branches (34–39) shipped 2026-08-05.** The feature is live. See "What actually shipped" at the top of this file for the current surface. If you're picking this up cold:

1. Read "What actually shipped" (top of this file) — that's the source of truth for what exists, not the Rollout table below.
2. The Rollout table + everything under it is preserved for design-history reasons but is no longer active planning. Deviations from it are called out in "What actually shipped".
3. Real-file smoke: `uv run --env-file .env python scripts/archive_smoke.py` — round-trips a few rows to prove end-to-end plumbing still works after any change.
4. Live surface to poke: `/admin/archive` (search & restore · bulk archive by date · bulk restore by date · running ops · repair). Files page has Active / Archived / All chips + bulk archive/restore for admin+operator; viewers never see them.
5. Deferred nice-to-haves are listed at the top under "Known nice-to-haves". Nothing here is a bug; each is a scope decision.

Current DB state after the first real bulk (2026-08-05): 91 archived, 13,401 active.
