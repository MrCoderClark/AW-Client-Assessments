# Plan

Phased build. Each phase is a merge-able unit. Mark items ✅ when done, 🚧 when in progress.

---

## Phase 1 — Backend index ✅

- ✅ uv project + smbprotocol dependency
- ✅ Connect to one PC via SMB, list C$
- ✅ Enumerate PDFs on `Users\Client\{Desktop,Documents,Downloads}` filtered by current-week window
- ✅ Extract PDF text (pypdf, first 3 pages)
- ✅ Port classifier from .ps1 (assessment type + name, PDF text patterns + filename fallbacks)
- ✅ SQLite index (`data.db`, table `pdfs`) — idempotent, skips unchanged files
- ✅ Loop over all 24 PCs with short connect timeout
- ✅ Remote-kill Adobe on locked files (`taskkill /S <host>` via RPC)

## Phase 2 — Backend commit ✅

- ✅ Copy → verify (size match) → delete, gated on assessment being detected
- ✅ Duplicate detection by MD5 (source deleted, dest points at original)
- ✅ Dest filename collision handled (`_1`, `_2`, …)
- ✅ Schema migration: `committed_at`, `dest_path` columns

## Phase 3 — API wrapper ✅

- ✅ FastAPI app (`api.py`) with CORS for local dev
- ✅ `GET /api/health`
- ✅ `GET /api/pdfs` — list index rows
- ✅ `POST /api/scans` — SSE stream of scan log
- ✅ `POST /api/commits` — SSE stream of commit log
- ✅ CLI scripts refactored to generators so API and CLI share one code path

## Phase 4 — Frontend v2 (DocFlow-style shell) ✅

Match `docs/Designs/Demo.jpg`. Not a rewrite of shadcn defaults.

- ✅ Dark navy sidebar (fixed 220px): brand mark, nav (Dashboard · Files · PCs · Logs · Settings)
- ✅ Top bar per page: title, notifications bell + user avatar, Scan / Commit / Log actions
- ✅ Dashboard page (`/`): 4 stat tiles across top, two-column widget grid below (Quick Actions + Recent Files)
- ✅ Files page (`/files`): sortable/searchable table with file-type icons + status pills; split-view viewer on row click
- ✅ Log drawer (right-side): SSE stream from `/api/scans` and `/api/commits`, color-coded lines
- ✅ Design tokens frozen (see `docs/SPEC.md` → Design tokens)
- ✅ Favicon + sidebar logo (shared `app/icon.svg` — blue rounded square + white document mark)

## Phase 5 — PC status + scan history 🚧

- ✅ Backend: `scan_runs` table (id, started_at, ended_at, mode, counts json, error)
- ✅ Backend: `pc_status` table upserted per-PC on every scan
- ✅ Backend: `GET /api/pcs` (24 rows, includes never-scanned)
- ✅ Backend: `GET /api/runs` (recent scan/commit history)
- ✅ Frontend: PCs page — grid with reachable/unreachable/never dot + last-seen + files-indexed
- ✅ Frontend: PCs nav item un-SOON'd
- ⬜ Dashboard chart: files indexed per day (last 14 days) from `scan_runs`

## Phase 6 — Scheduling from UI (kill Task Scheduler) ✅

- ✅ Backend: `scheduler.py` — plain asyncio loop (30s interval), no APScheduler dep
- ✅ Backend: `schedule` singleton table (`enabled`, `mode`, `time_of_day`, `weekdays`, `last_run_at`)
- ✅ Backend: `GET/PUT /api/schedule` with computed `next_run_at`
- ✅ FastAPI `lifespan` hook spawns the loop at startup
- ✅ Frontend: Settings page — enable toggle, time input, weekday chips, mode selector, Save, Run-now
- ✅ Settings nav item un-SOON'd

## Phase 7 — Email report (feature parity with .ps1) ✅

- ✅ Backend: `email_report.py` (stdlib smtplib, plain-text body — no HTML template dep)
- ✅ Config in `.env` (SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, EMAIL_FROM, EMAIL_TO)
- ✅ `email_on_commit` toggle in schedule row (migration added)
- ✅ Triggered from `commit_all` — send failures log to the SSE stream, never fail the commit
- ✅ Frontend: Notifications card in Settings with toggle

## Phase 8 — PDF viewer + edit ✅

- ✅ Backend: `GET /api/pdfs/{id}/content` — streams SMB bytes (dest if committed, source otherwise). Picks creds by host.
- ✅ Backend: `PATCH /api/pdfs/{id}` — rename destination file on the share + update DB row in one shot
- ✅ Frontend: `PdfPanel` / `PdfDrawer` — split-view viewer on Files page; modal drawer on Dashboard + PC-detail
- ✅ Inline Edit form (first / last name) with live filename preview and collision-safe rename

## Phase 9 — Logs page ✅

- ✅ Backend: `GET /api/logs` — per-PC folder breakdown (Desktop / Documents / Downloads / Other), total, committed, pending, joined with `pc_status`
- ✅ Frontend: `/logs` — 5 header stat tiles, sortable table with search, "All PCs" / "With files only" chips, auto-refresh every 15s
- ✅ PC-detail modal: click a PC row → drill-down with per-file breakdown, nested PDF viewer, folder detection (Desktop / Documents / Downloads / Other)

---

## Phase 10.5 — Per-PC File Explorer ✅

Path-restricted CRUD over SMB into `Users\Client\{Desktop,Documents,Downloads}`.

- ✅ Backend endpoints (path-guarded, no traversal, roots enforced):
  - `GET /api/pcs/{pc}/browse?path=…` — list folder
  - `GET /api/pcs/{pc}/file?path=…` — stream download
  - `POST /api/pcs/{pc}/upload?path=…` (multipart) — upload one or many files
  - `POST /api/pcs/{pc}/mkdir?path=…` — create folder
  - `PATCH /api/pcs/{pc}/rename?path=…` — rename file or folder
  - `DELETE /api/pcs/{pc}/delete?path=…` — delete file or empty folder
- ✅ Frontend: `PcExplorerDialog` modal — Desktop / Documents / Downloads tabs, breadcrumb, per-row actions (Download / Rename / Delete), toolbar (Upload / New Folder / Refresh), inline rename, confirm modals
- ✅ Wired: click a card on `/pcs` → opens explorer; "Open File Explorer" button in the Logs → PC-detail modal

## Phase 10 — Bulk actions on Files ✅

- ✅ Row checkboxes on Files page + tri-state select-all header
- ✅ Sticky bulk-action bar (appears when >0 selected): Commit selected · Export CSV · Delete… · Clear
- ✅ Confirm modal for delete with optional "Also delete file(s) on disk" checkbox
- ✅ Backend: `POST /api/pdfs/bulk` (`commit` | `delete`, ids, delete_files) — SSE progress
- ✅ Backend: `commit_all(only_ids=...)` param + `bulk.delete_rows(ids, delete_files)` generator
- ✅ Progress opens the shared log drawer (reuses existing SSE plumbing); refresh after done
- ✅ CSV export is client-side (no backend) — includes all key columns for the selected rows

## Phase 11 — Search & filters 🚧

- ✅ Global search in the top bar (⌘K / Ctrl+K) — `CommandPalette` component; fuzzy over PDFs (client + filename + host + assessment) and PCs (name + host); Enter routes to `/files?open=<id>` or `/pcs?open=<pc_name>` and both pages auto-open on that param
- ✅ Export current filter to CSV — "Export view" button in Files toolbar, exports the currently-filtered rows
- ⏸️ Files-page filter panel (multi-select for assessment/host, date/size ranges) — deferred: the existing search box already matches those fields; revisit if a real use case shows up
- ⏸️ Saved filter presets (localStorage) — YAGNI at ~500 files / one user
- ⏸️ Full-text search across extracted PDF text (SQLite FTS5) — biggest lift, unclear demand; unblock only if metadata search proves too coarse

## Phase 12 — Dashboard analytics ⬜

Rescue the deferred Phase 5 chart and build it out.

- ⬜ Files-per-day line chart (last 14 / 30 days) — pure SVG, no chart-lib dep
- ⬜ Files-by-assessment-type breakdown (horizontal bar)
- ⬜ Top PCs by contribution (small ranked list)
- ⬜ Time-to-commit histogram (index → commit gap) — surfaces stuck files

## Phase 13 — Reliability & operations 🚧

- ✅ Destination folder retention: **archiving + restore + repair, live 2026-08-05**. Files move to `\_Archive\MM-DD-YYYY\` on the same share (not `\Archive\YYYY\` as originally sketched — see `docs/ARCHIVING_PLAN.md` §D1). Manual trigger via `/admin/archive` (bulk-by-date + search-and-restore) and Files-page bulk buttons. Scheduled auto-archival deferred.
- ⬜ Daily `data.db` backup to the destination share (`\Backups\data-YYYY-MM-DD.db`)
- ⬜ `/api/health/full` — SMB dest reachable · SMB PC quorum · SMTP · DB writable · scheduler heartbeat
- ⬜ Install as Windows Service (nssm or native) so FastAPI starts on boot
- ⬜ Structured startup validation: warn (don't crash) if any expected env var is missing
- ⬜ Per-row "Rescan" (re-open source, re-classify, update row without re-download when unchanged)

## Phase 14 — Extended workflow ⬜

- ⬜ Force-rescan single PC on demand (button on `/pcs` row)
- ⬜ Manual PDF upload from the admin machine → lands in today's destination folder + creates a committed row
- ⬜ Notes / comments per file (audit trail — who edited what, when)
- ⬜ Assessment-type manager: view and add classifier patterns from UI (writes to a `classifier_patterns` table, `classify.py` reads DB first, code as fallback)
- ⬜ Weekly digest email (Monday summary — last week's totals, per-PC breakdown, files still pending)
- ⬜ Alert email if a PC has been unreachable for >N days

## Phase 15 — Authentication, roles, permissions ⬜

Deliberately last. LAN-only + single admin has been fine; only build this when a second user needs in.

- ⬜ `users` table (id, username, password_hash (bcrypt or passlib), role, created_at, disabled_at)
- ⬜ Login page (`/login`), session cookies (secure, httponly, sameSite=lax)
- ⬜ Roles:
  - `admin` — everything (user management, settings, delete-row)
  - `operator` — scan, commit, edit client names, view all
  - `viewer` — read-only (Dashboard, Files, PCs, Logs, PDF viewer)
- ⬜ Per-endpoint permission decorator; per-route protection on the frontend
- ⬜ User management page (admin only) — create / disable / reset password
- ⬜ Password reset via email (reuses existing SMTP config)
- ⬜ Audit log (append-only table + `/audit` page for admins): who ran scan/commit, who edited row, who changed schedule
- ⬜ Session timeout (e.g. 8h idle) with warning
- ⬜ Un-SOON the (still-hidden) User Management nav item

---

## Ideas parking lot

Loose thoughts that haven't earned a phase yet.

- **Scheduler catch-up guard** — if the backend boots N hours after today's scheduled slot and `last_run_at` is stale (< today's slot), fire immediately per current logic. Add a threshold (default 4h): if `now - today_slot > threshold`, skip and wait for tomorrow. Prevents a scheduled scan firing at 5 PM when the intent was 1 PM. Trigger: user request 2026-08-04 after diagnosing a stale-config misfire.
- **Audit schedule edits** — `set_schedule()` currently rewrites the row silently. Emit a `SCHEDULE_UPDATED` audit row (before + after values in `context_json`) so we can trace mystery schedule changes.
- **Scheduler run + skip notifications** — emit a `scan_commit` notification when the scheduler decides to fire and, at INFO/WARN, when it skips (with the reason: "already ran today's slot" / "not a scheduled weekday" / "disabled").
- Live-tail SSE for the scheduler loop (heartbeat every 30s in the sidebar dot)
- Duplicate-detection UI: show cluster of same-MD5 files across PCs
- Undo delete-row (soft delete + trash page)
- Keyboard shortcuts (`s` scan, `c` commit, `/` search, `?` help)
- Dark-mode explicit toggle (currently follows OS)
- Alternate destination shares (multi-share support)
- Redact-preview mode for PII in the viewer (blur names, show only assessment)
