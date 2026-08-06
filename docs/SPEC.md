# Spec

## What the app does

Discovers PDF client assessments on 24 lab PCs in the local `Users\Client\{Desktop,Documents,Downloads}` folders, classifies each by content + filename, and (on commit) copies the file — renamed — to `\\192.168.70.10\Client_Assessments\MM-DD-YYYY\` and deletes the source. Replaces `Lab_Client_Assessments_Backupv2.ps1`.

## Environment

Loaded from `.env` (single-quoted so backslashes in the domain user pass through):

```
SMB_USER='infotech'
SMB_PASS='<source-pc-password>'
DEST_SMB_USER='AWINYC\TrueNas'
DEST_SMB_PASS='<share-password>'
DEST_SHARE='\\192.168.70.10\Client_Assessments'   # optional override
```

## Data model — SQLite (`data.db`, table `pdfs`)

| column          | type    | notes                                       |
| --------------- | ------- | ------------------------------------------- |
| id              | INTEGER | PK                                          |
| host            | TEXT    | source PC IP                                |
| source_path     | TEXT    | full SMB path                               |
| filename        | TEXT    | original                                    |
| proposed_name   | TEXT    | classifier's rename target, null if skipped |
| assessment_type | TEXT    | e.g. `O_NET_Interest_Profiler`              |
| first_name      | TEXT    | nullable                                    |
| last_name       | TEXT    | nullable                                    |
| size            | INTEGER | bytes                                       |
| mtime           | TEXT    | ISO 8601                                    |
| md5             | TEXT    | dup detection                               |
| indexed_at      | TEXT    | when scan wrote the row                     |
| committed_at    | TEXT    | null = pending; set when copied + verified  |
| dest_path       | TEXT    | where it landed                             |

Constraint: `UNIQUE(host, source_path)` — one row per file.

## Classification (mirrors `.ps1` behavior)

Assessment type detection order (first match wins):

1. Spanish PDF-text patterns (`Perfil de intereses O*NET…` + sub-types)
2. English PDF-text patterns (`O*NET Interest Profiler…` + sub-types)
3. `VIA Character Strengths`
4. `StrengthsProfile`
5. Filename fallbacks: any of `O_NET`, `Perfil de intereses`, `VIA`, `StrengthsProfile`

Name detection order:

1. PDF text: `Printed for: <FirstName LastName>` / `Copia impresa para:`
2. PDF text: `Name: …` / `Nombre: …`
3. PDF text: 2-3 all-caps words near top **(VIA only, per .ps1)**
4. Filename patterns (13 shapes — see `classify.py::_FILENAME_PATTERNS`)

Handling:

- No assessment type → **skipped**, source untouched, row not written.
- No name → row written with `first_name = last_name = null`, `proposed_name` uses `Unknown-Client`.
- Duplicate (same MD5, already committed) → source deleted, row marked committed pointing at the original `dest_path`.

## API

Base URL: `http://localhost:8000` (dev) — LAN IP in prod.

| method | path                | shape                                                 |
| ------ | ------------------- | ----------------------------------------------------- |
| GET    | `/api/health`       | `{ok: true}`                                          |
| GET    | `/api/pdfs`         | `Pdf[]` (500 newest)                                  |
| POST   | `/api/scans`        | `text/event-stream` — `data: <line>\n\n`, ends `[DONE]` |
| POST   | `/api/commits`      | same shape                                            |

**Planned (Phase 5+):**

| method | path                       | shape                             |
| ------ | -------------------------- | --------------------------------- |
| GET    | `/api/pcs`                 | per-PC last-seen + counts         |
| GET    | `/api/runs`                | scan/commit history for the chart |
| GET    | `/api/schedules`           | Settings-page CRUD                |
| POST   | `/api/schedules`           |                                   |
| DELETE | `/api/schedules/{id}`      |                                   |
| GET    | `/api/pdfs/{id}/content`   | streams the committed dest bytes  |

## Frontend — design tokens

Locked from `docs/Designs/Demo.jpg`.

**Palette (light):**

```
--sidebar:   #0f1729   /* deep navy — sidebar background */
--sidebar-2: #1a2540   /* nav-item hover */
--sidebar-ink: #e5e7eb /* sidebar text */
--sidebar-muted: #94a3b8

--bg:       #f8fafc    /* main workspace */
--surface:  #ffffff    /* cards, table */
--border:   #e2e8f0
--border-strong: #cbd5e1

--ink:      #0f172a    /* body text */
--muted:    #64748b

--accent:   #2563eb    /* primary buttons, active nav marker */
--accent-hover: #1d4ed8

--ok:   #16a34a
--warn: #ca8a04
--err:  #dc2626
```

**Dark mode:** invert bg/surface/ink; keep sidebar palette identical (already dark).

**Typography:** Geist Sans (14px body), Geist Mono (12px for paths/filenames/hashes).

**Radii:** 4px on cards, 3px on buttons/inputs (not 12px pill — DMS not marketing).

**Density:** table rows 40px, sidebar items 34px, buttons 32px tall.

## Frontend — routes

| route     | purpose                                                            |
| --------- | ------------------------------------------------------------------ |
| `/`       | Dashboard — 4 stat tiles + Quick Actions + Recent Files            |
| `/files`  | Full sortable/searchable table                                     |
| `/pcs`    | (Phase 5) 24-PC grid with online/offline + last-seen               |
| `/settings` | (Phase 6) schedules + creds config                               |

Log drawer is global (right-side Radix Dialog), reachable from every page's top-bar Scan/Commit buttons.

## Non-goals (right now)

- Real auth (single admin, LAN-only)
- Mobile / tablet responsive polish
- PDF annotation, editing, sharing
- Team features (owners, comments, permissions)
- Any of the v1 sprawl (Chat, AI, Favorites, Trash, Sessions, Activity Log, RBAC)
