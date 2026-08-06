"""FastAPI wrapper around scan.py and commit.py, plus schedule + PC status.

Endpoints:
  GET  /api/health       — liveness
  GET  /api/pdfs         — list index rows
  GET  /api/pcs          — per-PC status (24 rows, includes never-scanned)
  GET  /api/runs         — recent scan/commit runs
  GET  /api/schedule     — current schedule + computed next_run
  PUT  /api/schedule     — update schedule
  POST /api/scans        — trigger a scan, stream progress as SSE
  POST /api/commits      — trigger a commit, stream progress as SSE

Run:
  uv run --env-file .env uvicorn api:app --reload --host 0.0.0.0 --port 8000
"""
import asyncio
import json
import os
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import PureWindowsPath
from typing import Iterator

import smbclient
import smbclient.path as smbpath
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from auth import errors as auth_errors
from auth.admin_routes import router as admin_users_router
from auth.context import AuthContext
from auth.engine import dispose as dispose_auth_engine, engine as auth_engine
from auth.http_security import CsrfMiddleware, SecurityHeadersMiddleware
from auth.notification_routes import router as notifications_router
from auth.notifications import NotificationEvent, emit_sync, register_main_loop
from auth.observability import RequestIdMiddleware, configure_logging
from auth.permissions import current_user_dep, require
from auth.profile_routes import router as profiles_router
from auth.routes import router as auth_router
from bulk import delete_rows
from commit import commit_all
from db import connect, get_schedule, set_schedule
from pcs import PCS
from scan import scan_all
from scheduler import compute_next_run, scheduler_loop
import archive as archive_svc


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ponytail: touch the engine at startup so a bad DATABASE_URL fails
    # loudly on boot instead of on the first auth request.
    configure_logging(os.environ.get("LOG_LEVEL", "INFO"))
    auth_engine()
    register_main_loop(asyncio.get_running_loop())
    task = asyncio.create_task(scheduler_loop())
    yield
    task.cancel()
    await dispose_auth_engine()


app = FastAPI(
    title="Client Files Viewer API",
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)

# Phase 15 auth: RFC 9457 error shape + /api/v1/auth/* endpoints + admin users
auth_errors.register(app)
app.include_router(auth_router)
app.include_router(admin_users_router)
app.include_router(profiles_router)
app.include_router(notifications_router)

# Middleware order runs OUTER → INNER when a request comes in. add_middleware
# stacks in reverse of declaration, so declare in the order you want them to
# run OUTERMOST first — CORS wraps everything (preflight replies), then
# security headers (stamp every response), then CSRF (last, closest to route).
_allowed_origins = [
    o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "http://localhost:3000").split(",")
    if o.strip()
]
app.add_middleware(CsrfMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestIdMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_methods=["*"],
    allow_headers=["*", "X-CSRF-Token", "X-Request-ID"],
    allow_credentials=True,
    expose_headers=["X-Request-ID"],
)


def _sse(lines: Iterator[str]) -> Iterator[bytes]:
    for line in lines:
        yield f"data: {line}\n\n".encode()
    yield b"data: [DONE]\n\n"


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/pdfs")
def list_pdfs(archived: str = "false", ctx: AuthContext = current_user_dep()):
    """?archived=false (default, active only) | true (archived only) | all.

    Viewing archived rows requires `pdf:archive` (admin + operator). A viewer
    passing `archived=true|all` gets a 403 — matches the "hidden from every
    frontend surface" guarantee in docs/ARCHIVING_PLAN.md D4.
    """
    if "pdf:read" not in ctx.permissions:
        raise HTTPException(403, "missing permission: pdf:read")
    if archived != "false" and "pdf:archive" not in ctx.permissions:
        raise HTTPException(403, "missing permission: pdf:archive")
    # ponytail: no LIMIT — pagination is client-side, dataset is single-digit MB even at 10k rows.
    # Upgrade path: server-side ?page=&size= if we ever cross ~50k rows.
    if archived == "true":
        where = "WHERE archived_at IS NOT NULL"
    elif archived == "all":
        where = ""
    else:
        where = "WHERE archived_at IS NULL"
    conn = connect()
    rows = conn.execute(f"""
        SELECT id, host, source_path, filename, proposed_name, assessment_type,
               first_name, last_name, size, mtime, md5,
               indexed_at, committed_at, dest_path,
               archived_at, archive_path, archive_status
        FROM pdfs
        {where}
        ORDER BY indexed_at DESC
    """).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/pcs")
def list_pcs(_=require("pc:read")):
    conn = connect()
    status = {r["pc_name"]: dict(r) for r in conn.execute("SELECT * FROM pc_status")}
    # Archived rows excluded from surface counts — docs/ARCHIVING_PLAN.md D4.
    file_counts = {r["host"]: r["n"] for r in conn.execute(
        "SELECT host, COUNT(*) AS n FROM pdfs WHERE archived_at IS NULL GROUP BY host"
    )}
    result = []
    for pc_name, host in PCS.items():
        s = status.get(pc_name)
        # JSONB → psycopg gives us the decoded value directly.
        counts = s["last_counts_json"] if s and s.get("last_counts_json") else None
        result.append({
            "pc_name":       pc_name,
            "host":          host,
            "last_attempt":  s.get("last_attempt") if s else None,
            "last_seen":     s.get("last_seen") if s else None,
            "reachable":     bool(s.get("last_reachable")) if s and s.get("last_reachable") is not None else None,
            "error":         s.get("last_error") if s else None,
            "counts":        counts,
            "files_indexed": file_counts.get(host, 0),
        })
    return result


@app.get("/api/logs")
def list_logs(_=require("log:read")):
    """Per-PC folder breakdown (Desktop / Documents / Downloads) + status + pending count.
    Numbers reflect what's currently in the index (all-time)."""
    conn = connect()

    # Folder breakdown per host, split by committed status.
    # Archived rows excluded from surface counts — docs/ARCHIVING_PLAN.md D4.
    rows = conn.execute(r"""
        SELECT
          host,
          CASE
            WHEN source_path LIKE '%\Desktop\%'    THEN 'desktop'
            WHEN source_path LIKE '%\Documents\%'  THEN 'documents'
            WHEN source_path LIKE '%\Downloads\%'  THEN 'downloads'
            ELSE 'other'
          END AS folder,
          COUNT(*) AS total,
          SUM(CASE WHEN committed_at IS NOT NULL THEN 1 ELSE 0 END) AS committed
        FROM pdfs
        WHERE archived_at IS NULL
        GROUP BY host, folder
    """).fetchall()

    per_host: dict[str, dict] = {}
    for r in rows:
        h = r["host"]
        d = per_host.setdefault(h, {
            "desktop": 0, "documents": 0, "downloads": 0, "other": 0,
            "total": 0, "committed": 0,
        })
        d[r["folder"]] = r["total"]
        d["total"] += r["total"]
        d["committed"] += r["committed"] or 0

    status = {r["pc_name"]: dict(r) for r in conn.execute("SELECT * FROM pc_status")}

    result = []
    for pc_name, host in PCS.items():
        c = per_host.get(host, {"desktop": 0, "documents": 0, "downloads": 0, "other": 0, "total": 0, "committed": 0})
        s = status.get(pc_name)
        result.append({
            "pc_name":      pc_name,
            "host":         host,
            "desktop":      c["desktop"],
            "documents":    c["documents"],
            "downloads":    c["downloads"],
            "other":        c["other"],
            "total":        c["total"],
            "committed":    c["committed"],
            "pending":      c["total"] - c["committed"],
            "last_attempt": s.get("last_attempt") if s else None,
            "last_seen":    s.get("last_seen") if s else None,
            "reachable":    bool(s.get("last_reachable")) if s and s.get("last_reachable") is not None else None,
            "error":        s.get("last_error") if s else None,
        })
    return result


@app.get("/api/runs")
def list_runs(limit: int = 20, _=require("run:read")):
    conn = connect()
    rows = conn.execute(
        "SELECT id, mode, started_at, ended_at, counts_json, error FROM scan_runs ORDER BY id DESC LIMIT %s",
        (limit,),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["counts"] = d.pop("counts_json")  # already a dict via JSONB
        out.append(d)
    return out


class SchedulePatch(BaseModel):
    enabled: bool | None = None
    mode: str | None = Field(default=None, pattern="^(scan|scan\\+commit)$")
    time_of_day: str | None = Field(default=None, pattern="^\\d{2}:\\d{2}$")
    weekdays: str | None = Field(default=None, pattern="^([0-6](,[0-6])*)?$")
    email_on_commit: bool | None = None


def _schedule_with_next(conn) -> dict:
    s = get_schedule(conn)
    s["next_run_at"] = compute_next_run(s)
    return s


@app.get("/api/schedule")
def read_schedule(_=require("schedule:read")):
    return _schedule_with_next(connect())


@app.put("/api/schedule")
def update_schedule(patch: SchedulePatch, _=require("schedule:write")):
    conn = connect()
    updates = {k: v for k, v in patch.model_dump().items() if v is not None}
    # ponytail: schedule columns are real BOOLEAN in Postgres — pass through.
    set_schedule(conn, **updates)
    return _schedule_with_next(conn)


_PDF_CHUNK = 64 * 1024


def _register_smb_for_path(host: str) -> None:
    """Pick the right creds by whether the host is our destination share or a source PC."""
    dest_share = os.environ.get("DEST_SHARE", r"\\192.168.70.10\Client_Assessments")
    dest_host = dest_share.lstrip("\\").split("\\", 1)[0]
    if host == dest_host:
        u, p = os.environ.get("DEST_SMB_USER"), os.environ.get("DEST_SMB_PASS")
        timeout = 10
    else:
        u, p = os.environ.get("SMB_USER"), os.environ.get("SMB_PASS")
        timeout = 5
    if not (u and p):
        raise HTTPException(500, "SMB creds missing from environment")
    smbclient.register_session(host, username=u, password=p, connection_timeout=timeout)


class PdfPatch(BaseModel):
    first_name: str | None = None
    last_name: str | None = None


def _title(s: str | None) -> str | None:
    if not s:
        return None
    s = s.strip()
    return s.title() if s else None


@app.patch("/api/pdfs/{pdf_id}")
def update_pdf(pdf_id: int, patch: PdfPatch, _=require("pdf:write")):
    """Rename a committed PDF (on the share + in the DB) or update a pending row (DB only).

    Recomputes proposed_name from assessment_type + first_name + last_name.
    409 if the target filename would collide with an existing file on the share.
    """
    conn = connect()
    row = conn.execute("SELECT * FROM pdfs WHERE id = %s", (pdf_id,)).fetchone()
    if not row:
        raise HTTPException(404, "not found")
    if row["archived_at"] is not None:
        raise HTTPException(409, "cannot rename an archived file — restore it first")
    if not row["assessment_type"]:
        raise HTTPException(400, "row has no assessment_type — cannot rebuild filename")

    first = _title(patch.first_name)
    last = _title(patch.last_name)
    name_part = f"{first}_{last}" if (first and last) else "Unknown-Client"
    new_proposed = f"{row['assessment_type']}-{name_part}.pdf"

    new_dest = row["dest_path"]
    if row["dest_path"] and not row["dest_path"].endswith(new_proposed):
        dest_folder = row["dest_path"].rsplit("\\", 1)[0]
        candidate = f"{dest_folder}\\{new_proposed}"
        host = candidate.lstrip("\\").split("\\", 1)[0]
        _register_smb_for_path(host)
        # Collision check
        try:
            smbclient.stat(candidate)
            raise HTTPException(409, f"target already exists on share: {new_proposed}")
        except HTTPException:
            raise
        except OSError:
            pass  # good — target doesn't exist yet
        try:
            smbclient.rename(row["dest_path"], candidate)
        except Exception as e:
            raise HTTPException(500, f"rename failed: {e.__class__.__name__}: {e}")
        new_dest = candidate

    conn.execute(
        "UPDATE pdfs SET first_name = ?, last_name = ?, proposed_name = ?, dest_path = ? WHERE id = ?",
        (first, last, new_proposed, new_dest, pdf_id),
    )
    conn.commit()
    updated = conn.execute("SELECT * FROM pdfs WHERE id = %s", (pdf_id,)).fetchone()
    return dict(updated)


@app.get("/api/pdfs/{pdf_id}/content")
def pdf_content(pdf_id: int, download: bool = False, ctx: AuthContext = current_user_dep()):
    """Stream PDF bytes from wherever it lives — dest share if committed, source PC otherwise.
    Pass ?download=1 to force the browser to save instead of render inline.

    Archived rows require `pdf:archive` on top of `pdf:read`.
    """
    if "pdf:read" not in ctx.permissions:
        raise HTTPException(403, "missing permission: pdf:read")
    conn = connect()
    row = conn.execute(
        "SELECT filename, proposed_name, source_path, dest_path, archive_path, archived_at "
        "FROM pdfs WHERE id = %s",
        (pdf_id,),
    ).fetchone()
    if not row:
        raise HTTPException(404, "not found")
    if row["archived_at"] is not None and "pdf:archive" not in ctx.permissions:
        # Same 404 shape a viewer gets for a missing row — don't leak existence.
        raise HTTPException(404, "not found")

    # Archived rows live at archive_path (dest_path is preserved so restore
    # knows where to put them back). Serve from archive_path when set.
    path = row["archive_path"] or row["dest_path"] or row["source_path"]
    if not path:
        raise HTTPException(404, "no path recorded")
    # SMB path shape: \\host\share\...
    host = path.lstrip("\\").split("\\", 1)[0]
    _register_smb_for_path(host)

    try:
        stat = smbclient.stat(path)
    except Exception as e:
        raise HTTPException(404, f"file not accessible: {e.__class__.__name__}: {e}")

    display_name = row["proposed_name"] or PureWindowsPath(row["filename"]).name

    def stream():
        with smbclient.open_file(path, mode="rb") as f:
            while True:
                chunk = f.read(_PDF_CHUNK)
                if not chunk:
                    break
                yield chunk

    return StreamingResponse(
        stream(),
        media_type="application/pdf",
        headers={
            "Content-Length": str(stat.st_size),
            "Content-Disposition": f'{"attachment" if download else "inline"}; filename="{display_name}"',
            # ponytail: cache within one page load; every scan can change committed state
            "Cache-Control": "private, max-age=30",
        },
    )


def _emit_run_completion(kind: str, lines: list[str]) -> None:
    """After a scan/commit generator drains, notify everyone with a compact
    summary. `lines` is the raw log tail we already streamed to the caller."""
    tail = [ln for ln in lines[-6:] if ln]
    summary = tail[-1] if tail else f"{kind} finished"
    # Best-effort severity — if any line contains 'unreachable' or 'error', warn.
    joined = "\n".join(tail).lower()
    severity = "WARN" if ("unreachable" in joined or "error" in joined or "fail" in joined) else "INFO"
    emit_sync(NotificationEvent(
        category="scan_commit",
        kind=f"{kind}_completed",
        severity=severity,
        title=f"{kind.capitalize()} completed",
        body="\n".join(tail),
        url="/logs",
        context={"summary": summary},
    ))


def _sse_with_notify(kind: str, source):
    lines: list[str] = []
    try:
        for line in source:
            lines.append(line)
            yield f"data: {line}\n\n".encode()
        yield b"data: [DONE]\n\n"
    finally:
        # Emit even on early client disconnect / error — the run itself may
        # have completed even if the SSE consumer went away.
        try:
            _emit_run_completion(kind, lines)
        except Exception as e:  # pragma: no cover
            print(f"[notify] emit_run_completion({kind}) failed: {e}")


@app.post("/api/scans")
def start_scan(_=require("run:trigger")):
    return StreamingResponse(_sse_with_notify("scan", scan_all()), media_type="text/event-stream")


@app.post("/api/commits")
def start_commit(_=require("run:trigger")):
    return StreamingResponse(_sse_with_notify("commit", commit_all()), media_type="text/event-stream")


# ---------- PC file-explorer ----------

_EXPLORER_ROOTS = ("Desktop", "Documents", "Downloads")


def _explorer_path(pc_name: str, rel: str) -> tuple[str, str]:
    """Turn ('PC1', 'Downloads/foo/bar.pdf') into full SMB path + host, with guardrails."""
    if pc_name not in PCS:
        raise HTTPException(404, f"unknown PC: {pc_name}")
    parts = [p for p in (rel or "").replace("\\", "/").split("/") if p]
    if not parts or parts[0] not in _EXPLORER_ROOTS:
        raise HTTPException(400, f"path must start with one of {_EXPLORER_ROOTS}")
    if ".." in parts:
        raise HTTPException(400, "path traversal not allowed")
    host = PCS[pc_name]
    return rf"\\{host}\C$\Users\Client\\" + "\\".join(parts), host


@app.get("/api/pcs/{pc_name}/browse")
def browse(pc_name: str, path: str = "Desktop", _=require("pc:browse")):
    full, host = _explorer_path(pc_name, path)
    _register_smb_for_path(host)
    try:
        entries = []
        for e in smbclient.scandir(full):
            try:
                st = e.stat()
                is_dir = e.is_dir()
                entries.append({
                    "name": e.name,
                    "is_dir": is_dir,
                    "size": None if is_dir else st.st_size,
                    "mtime": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
                })
            except OSError:
                continue
        entries.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
        return {"path": path, "entries": entries}
    except OSError as e:
        raise HTTPException(400, f"cannot browse: {e.__class__.__name__}: {e}")


@app.get("/api/pcs/{pc_name}/file")
def download_file(pc_name: str, path: str, _=require("pc:browse")):
    full, host = _explorer_path(pc_name, path)
    _register_smb_for_path(host)
    try:
        stat = smbclient.stat(full)
    except OSError as e:
        raise HTTPException(404, f"not found: {e}")
    name = full.rsplit("\\", 1)[-1]

    def stream():
        with smbclient.open_file(full, mode="rb") as f:
            while True:
                chunk = f.read(_PDF_CHUNK)
                if not chunk:
                    break
                yield chunk

    return StreamingResponse(
        stream(),
        media_type="application/octet-stream",
        headers={
            "Content-Length": str(stat.st_size),
            "Content-Disposition": f'attachment; filename="{name}"',
        },
    )


@app.post("/api/pcs/{pc_name}/upload")
async def upload_file(pc_name: str, path: str, file: UploadFile = File(...), _=require("pc:upload")):
    """path is the target FOLDER (e.g. 'Downloads'). File saved as file.filename inside it."""
    full, host = _explorer_path(pc_name, path)
    _register_smb_for_path(host)
    if not smbpath.isdir(full):
        raise HTTPException(400, "target path is not a folder")
    if not file.filename or "\\" in file.filename or "/" in file.filename:
        raise HTTPException(400, "invalid filename")
    dest = f"{full}\\{file.filename}"
    if smbpath.exists(dest):
        raise HTTPException(409, f"already exists: {file.filename}")
    try:
        with smbclient.open_file(dest, mode="wb") as fout:
            while True:
                chunk = await file.read(_PDF_CHUNK)
                if not chunk:
                    break
                fout.write(chunk)
        return {"ok": True, "name": file.filename}
    except Exception as e:
        raise HTTPException(500, f"upload failed: {e.__class__.__name__}: {e}")


class MkdirReq(BaseModel):
    name: str


@app.post("/api/pcs/{pc_name}/mkdir")
def mkdir(pc_name: str, path: str, req: MkdirReq, _=require("pc:upload")):
    """`path` is the parent folder; `req.name` is the new folder name."""
    full, host = _explorer_path(pc_name, path)
    _register_smb_for_path(host)
    if not req.name or "\\" in req.name or "/" in req.name or req.name in (".", ".."):
        raise HTTPException(400, "invalid folder name")
    target = f"{full}\\{req.name}"
    if smbpath.exists(target):
        raise HTTPException(409, "already exists")
    try:
        smbclient.mkdir(target)
        return {"ok": True}
    except OSError as e:
        raise HTTPException(400, f"mkdir failed: {e}")


class RenameReq(BaseModel):
    new_name: str


@app.patch("/api/pcs/{pc_name}/rename")
def rename_entry(pc_name: str, path: str, req: RenameReq, _=require("pc:write")):
    full, host = _explorer_path(pc_name, path)
    _register_smb_for_path(host)
    if not req.new_name or "\\" in req.new_name or "/" in req.new_name or req.new_name in (".", ".."):
        raise HTTPException(400, "invalid name")
    parent = full.rsplit("\\", 1)[0]
    new_full = f"{parent}\\{req.new_name}"
    if smbpath.exists(new_full):
        raise HTTPException(409, "target already exists")
    try:
        smbclient.rename(full, new_full)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(500, f"rename failed: {e.__class__.__name__}: {e}")


@app.delete("/api/pcs/{pc_name}/delete")
def delete_entry(pc_name: str, path: str, _=require("pc:write")):
    full, host = _explorer_path(pc_name, path)
    _register_smb_for_path(host)
    if not smbpath.exists(full):
        raise HTTPException(404, "not found")
    try:
        if smbpath.isdir(full):
            smbclient.rmdir(full)  # only empty dirs
        else:
            smbclient.remove(full)
        return {"ok": True}
    except OSError as e:
        raise HTTPException(400, f"delete failed (folder must be empty): {e}")


class BulkRequest(BaseModel):
    action: str = Field(pattern="^(commit|delete)$")
    ids: list[int]
    delete_files: bool = False


@app.post("/api/pdfs/bulk")
def bulk_action(req: BulkRequest, ctx: AuthContext = current_user_dep()):
    # Two actions with different permissions — check inline against the caller's
    # permission set. Operators can commit; only admins can delete rows.
    if not req.ids:
        raise HTTPException(400, "no ids provided")
    if req.action == "commit":
        if "run:trigger" not in ctx.permissions:
            raise HTTPException(403, "missing permission: run:trigger")
        return StreamingResponse(_sse(commit_all(only_ids=req.ids)), media_type="text/event-stream")
    if req.action == "delete":
        if "pdf:delete" not in ctx.permissions:
            raise HTTPException(403, "missing permission: pdf:delete")
        return StreamingResponse(_sse(delete_rows(req.ids, delete_files=req.delete_files)), media_type="text/event-stream")
    raise HTTPException(400, f"unknown action: {req.action}")


# ---------- Archive + Restore (docs/ARCHIVING_PLAN.md) --------------

class IdListRequest(BaseModel):
    ids: list[int]


class DateRangeRequest(BaseModel):
    before: datetime
    after: datetime | None = None


class RestoreDateRangeRequest(DateRangeRequest):
    date_field: str = Field(default="archived_at", pattern="^(archived_at|committed_at)$")


class ArchiveSearchRequest(BaseModel):
    first_name: str | None = None
    last_name: str | None = None
    filename: str | None = None
    assessment_type: str | None = None
    before: datetime | None = None
    after: datetime | None = None
    date_field: str = Field(default="committed_at", pattern="^(committed_at|archived_at)$")
    limit: int = Field(default=50, ge=1, le=500)
    offset: int = Field(default=0, ge=0)


@app.post("/api/pdfs/bulk/archive")
def bulk_archive(req: IdListRequest, ctx: AuthContext = require("pdf:archive")):
    if not req.ids:
        raise HTTPException(400, "no ids provided")
    return archive_svc.archive_ids(req.ids, actor_id=str(ctx.user_id) if ctx.user_id else None)


@app.post("/api/pdfs/bulk/restore")
def bulk_restore(req: IdListRequest, ctx: AuthContext = require("pdf:archive")):
    if not req.ids:
        raise HTTPException(400, "no ids provided")
    return archive_svc.restore_ids(req.ids, actor_id=str(ctx.user_id) if ctx.user_id else None)


def _preview_sample(direction: str, before: datetime, after: datetime | None,
                    date_field: str = "committed_at") -> dict:
    """Cheap sanity-check for the archive/restore date pickers: total
    matching count + ≤10 sample filenames. Never returns the full list."""
    if direction == "archive":
        where = ["committed_at < %s", "archived_at IS NULL"]
        params: list = [before]
        if after is not None:
            where.insert(0, "committed_at >= %s")
            params.insert(0, after)
    else:
        col = date_field if date_field in ("committed_at", "archived_at") else "archived_at"
        where = [f"{col} < %s", "archived_at IS NOT NULL", "archive_status = 'archived'"]
        params = [before]
        if after is not None:
            where.insert(0, f"{col} >= %s")
            params.insert(0, after)
    wh = " AND ".join(where)
    conn = connect()
    try:
        n = conn.execute(f"SELECT COUNT(*) AS n FROM pdfs WHERE {wh}", tuple(params)).fetchone()
        rows = conn.execute(
            f"SELECT filename FROM pdfs WHERE {wh} ORDER BY id LIMIT 10", tuple(params),
        ).fetchall()
    finally:
        conn.close()
    return {"count": int(n["n"]) if n else 0,
            "sample": [r["filename"] for r in rows]}


@app.post("/api/pdfs/archive-preview")
def archive_preview(req: DateRangeRequest, _=require("pdf:archive")):
    return _preview_sample("archive", req.before, req.after)


@app.post("/api/pdfs/restore-preview")
def restore_preview(req: RestoreDateRangeRequest, _=require("pdf:archive")):
    return _preview_sample("restore", req.before, req.after, req.date_field)


@app.post("/api/pdfs/archive-search")
def archive_search(req: ArchiveSearchRequest, _=require("pdf:archive")):
    """Search archived rows. At least one filter is required — an empty
    body returns 400 so nobody can "restore everything" by accident."""
    if not any([req.first_name, req.last_name, req.filename, req.assessment_type,
                req.before, req.after]):
        raise HTTPException(400, "at least one search criterion is required")

    where = ["archived_at IS NOT NULL", "archive_status = 'archived'"]
    params: list = []
    if req.first_name:
        where.append("first_name ILIKE %s")
        params.append(f"%{req.first_name}%")
    if req.last_name:
        where.append("last_name ILIKE %s")
        params.append(f"%{req.last_name}%")
    if req.filename:
        where.append("(filename ILIKE %s OR proposed_name ILIKE %s)")
        params.extend([f"%{req.filename}%", f"%{req.filename}%"])
    if req.assessment_type:
        where.append("assessment_type = %s")
        params.append(req.assessment_type)
    if req.after is not None:
        where.append(f"{req.date_field} >= %s")
        params.append(req.after)
    if req.before is not None:
        where.append(f"{req.date_field} < %s")
        params.append(req.before)
    wh = " AND ".join(where)

    conn = connect()
    try:
        total = conn.execute(f"SELECT COUNT(*) AS n FROM pdfs WHERE {wh}", tuple(params)).fetchone()
        rows = conn.execute(
            f"""SELECT id, host, filename, proposed_name, assessment_type,
                       first_name, last_name, committed_at, archived_at,
                       archive_path, dest_path
                FROM pdfs WHERE {wh}
                ORDER BY archived_at DESC, id DESC
                LIMIT %s OFFSET %s""",
            tuple(params) + (req.limit, req.offset),
        ).fetchall()
    finally:
        conn.close()
    return {"total": int(total["n"]) if total else 0,
            "limit": req.limit, "offset": req.offset,
            "results": [dict(r) for r in rows]}


import queue as _queue
import threading as _threading


_SSE_HEADERS = {
    # Signals to nginx/Next-dev-proxy to not buffer per-message.
    "X-Accel-Buffering": "no",
    "Cache-Control": "no-cache, no-transform",
}
_SSE_PAD = b":" + b" " * 2048 + b"\n\n"  # flush past small proxy buffers


def _run_gen_in_thread(gen, q: "_queue.Queue"):
    """Consume `gen` in a background thread, pushing each yielded frame to
    a queue. Puts `None` when done so the reader can stop cleanly.

    Decouples "who's doing the work" from "who's watching" — if the SSE
    reader stops (client closes the browser), the work continues to
    completion. The queue grows without a reader; frames are small dicts.
    """
    try:
        for frame in gen:
            q.put(frame)
    except Exception as e:  # noqa: BLE001
        q.put({"phase": "error", "reason": f"{e.__class__.__name__}: {e}"})
    finally:
        q.put(None)


def _archive_stream_sse(job_id: str, gen):
    """Spawn the archive work in a background thread and stream its
    progress frames to the client. Client disconnect ≠ archive halt.
    """
    q: "_queue.Queue" = _queue.Queue()
    t = _threading.Thread(target=_run_gen_in_thread, args=(gen, q), daemon=True)
    t.start()

    # 2KB pad so any 4KB-buffering proxy flushes the header block immediately.
    yield _SSE_PAD
    while True:
        try:
            frame = q.get(timeout=15)
        except _queue.Empty:
            # Keepalive comment. Client stays subscribed; if disconnected
            # this write raises and the generator terminates — but the
            # worker thread keeps running.
            yield b": keepalive\n\n"
            continue
        if frame is None:
            yield b"data: [DONE]\n\n"
            return
        yield f"data: {json.dumps(frame, default=str)}\n\n".encode()


@app.post("/api/pdfs/archive-by-date")
def archive_by_date(req: DateRangeRequest, ctx: AuthContext = require("pdf:archive")):
    job_id = archive_svc.register_job(
        "archive", before=req.before, after=req.after,
        actor_id=str(ctx.user_id) if ctx.user_id else None,
    )
    gen = archive_svc.archive_by_date_stream(
        before=req.before, after=req.after,
        actor_id=str(ctx.user_id) if ctx.user_id else None,
        job_id=job_id,
    )
    return StreamingResponse(
        _archive_stream_sse(job_id, gen), media_type="text/event-stream",
        headers={"X-Job-Id": job_id, **_SSE_HEADERS},
    )


@app.post("/api/pdfs/restore-by-date")
def restore_by_date(req: RestoreDateRangeRequest, ctx: AuthContext = require("pdf:archive")):
    job_id = archive_svc.register_job(
        "restore", before=req.before, after=req.after,
        actor_id=str(ctx.user_id) if ctx.user_id else None,
    )
    gen = archive_svc.restore_by_date_stream(
        before=req.before, after=req.after, date_field=req.date_field,
        actor_id=str(ctx.user_id) if ctx.user_id else None,
        job_id=job_id,
    )
    return StreamingResponse(
        _archive_stream_sse(job_id, gen), media_type="text/event-stream",
        headers={"X-Job-Id": job_id, **_SSE_HEADERS},
    )


@app.post("/api/pdfs/archive-jobs/{job_id}/cancel")
def archive_job_cancel(job_id: str, _=require("pdf:archive")):
    ok = archive_svc.request_cancel(job_id)
    if not ok:
        raise HTTPException(404, "job not found")
    return {"ok": True}


@app.get("/api/pdfs/archive-jobs")
def archive_jobs_list(_=require("pdf:archive")):
    """In-memory registry snapshot — running + recently finished jobs.
    Truncates the ids/context blobs so a large registry stays cheap."""
    out = []
    for job in archive_svc.list_jobs():
        out.append(job)
    return {"jobs": out}


# ---------- repair (docs/ARCHIVING_PLAN.md §Recovery) ----------------

@app.post("/api/pdfs/repair-check")
def repair_check(_=require("pdf:archive")):
    """Dry-run reconciliation scan. Returns counts per mismatch kind."""
    from repair import scan_all as _scan
    return _scan(fix=False)


@app.post("/api/pdfs/repair-apply")
def repair_apply(ctx: AuthContext = require("pdf:archive")):
    """Apply the reconciliation. Same shape as repair-check, plus a
    per-row "did_fix" tally."""
    from repair import scan_all as _scan
    return _scan(fix=True, actor_id=str(ctx.user_id) if ctx.user_id else None)
