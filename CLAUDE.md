# Client Files Viewer v2

Replaces the PowerShell script `Lab_Client_Assessments_Backupv2.ps1` at
AmericaWorks NYC labs. Discovers PDF client assessments on 24 lab PCs,
classifies + renames them, and (on commit) copies to a network share and
deletes the source.

## Stack

- **Backend:** Python 3.12 · uv · FastAPI · smbprotocol · pypdf · **Postgres** (`clientfiles_v2` on 192.168.70.10:5432 — async via SQLAlchemy+asyncpg for auth code, sync via psycopg for scan/commit/archive). Alembic owns migrations.
- **Frontend:** Next.js 16 (App Router) · TypeScript · Tailwind v4 · Radix primitives (no shadcn)
- **Design reference:** `docs/Designs/Demo.jpg` — DocFlow Pro aesthetic (dark navy sidebar, blue accent, dashboard widgets, professional DMS)
- **Design + build plan:** `docs/PLAN.md` · **Archive/restore:** `docs/ARCHIVING_PLAN.md` (shipped 2026-08-05) · **Auth:** `docs/PHASE15_AUTH.md` (in progress)

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
  auth/            Phase 15 auth: routes, service, audit, notifications, permissions
  backend/alembic/ migrations (schema owned by Alembic, not app code)
  scripts/         smokes + one-off tools (backfill_share, repair_archive_state, …)
  .env             SMB + DB creds (gitignored)
  frontend/        Next.js app
  docs/            PLAN.md, SPEC.md, ARCHIVING_PLAN.md, PHASE15_AUTH.md, Designs/
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
- **Auth is live (Phase 15 M1 + M2 audit).** Bootstrap admin: `admin@aw.local` / `Correct-Horse-Battery-9!`. Three roles: `admin`, `operator`, `viewer`. Permissions per role in `auth/permissions.py`.

## Explicitly out of scope (until asked)

- All v1 sprawl: Chat, AI Analysis, Favorites, Trash, Sessions, Activity Log widget bloat
- Cloud deployment (this runs on the LAN server)
- Mobile responsive polish (desktop-first internal tool)

## When continuing work

Read `docs/PLAN.md` first — it's the source of truth for what's done, in progress, and next. For feature-specific work, prefer the feature's dedicated plan (`docs/ARCHIVING_PLAN.md`, `docs/PHASE15_AUTH.md`) — those have a "What actually shipped" section at the top for anything that's landed, plus deferred / gotcha notes.

## SSE + BaseHTTPMiddleware gotcha

Streaming responses through `BaseHTTPMiddleware`-based middleware (Csrf, SecurityHeaders, RequestId — all three in the stack) get batched via anyio memory streams; frames flushed at end when yields are slow. Workarounds already applied in `api.py::_archive_stream_sse`: `X-Accel-Buffering: no`, `Cache-Control: no-cache, no-transform`, 2KB pad on first flush, 15s keepalive. If a new SSE endpoint yields slowly and stalls anyway, install `sse-starlette` and use `EventSourceResponse` (or convert the three middlewares to pure ASGI).
