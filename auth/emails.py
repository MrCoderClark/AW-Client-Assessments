"""HTML + plain-text emails via Jinja2 templates.

Templates live in `templates/email/`. Each template is a pair — `NAME.html`
extends `_base.html`, `NAME.txt` is the plain-text alternative. Both are
rendered with the same context and stitched into a `multipart/alternative`
message by `send_mail`.

Shared context, injected into every render:
  brand   → EMAIL_BRAND_NAME env or "Client Files Viewer"
  support → EMAIL_SUPPORT_LINE env or a sensible default
  c       → design tokens (colors + font stack) — used by _macros.html
  title   → per-template title (used in header + <title>)
  preheader → inbox-preview line

ponytail: no CSS-inlining library; templates hand-inline styles because
that's what mail clients render reliably. Autoescape is on for .html and
off for .txt.
"""
from __future__ import annotations

import os
import smtplib
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape


# ---------- config --------------------------------------------------

def _cfg(name: str) -> str | None:
    v = os.environ.get(name)
    return v.strip() if v and v.strip() else None


def app_base_url() -> str:
    return _cfg("APP_BASE_URL") or "http://localhost:3000"


def _brand_name() -> str:
    return _cfg("EMAIL_BRAND_NAME") or "Client Files Viewer"


def _support_line() -> str:
    return _cfg("EMAIL_SUPPORT_LINE") or "AmericaWorks NYC · Client Files Viewer"


# ---------- MailSpec ------------------------------------------------

@dataclass(frozen=True)
class MailSpec:
    subject: str
    body: str                              # text/plain fallback
    html: str | None = None                # text/html (preferred by mail clients)
    preheader: str = ""                    # short line shown in inbox preview


# ---------- Jinja2 environment --------------------------------------

_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates" / "email"

_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=select_autoescape(enabled_extensions=("html",), default=False),
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=True,
)


# Jinja2 filter — turns any datetime / ISO string / None into a friendly
# local-format timestamp: "07/27/2026 09:00:04 AM".
def humandate(v: Any) -> str:
    if v is None or v == "":
        return ""
    dt: datetime | None = None
    if isinstance(v, datetime):
        dt = v
    elif isinstance(v, str):
        try:
            dt = datetime.fromisoformat(v)
        except ValueError:
            return v  # unparseable → hand back what we got
    if dt is None:
        return str(v)
    return dt.strftime("%m/%d/%Y %I:%M:%S %p")


_env.filters["humandate"] = humandate


# Base tokens shared by every theme. Per-category themes override
# accent + accent_dk and add chip_bg / chip_fg / chip_label for the
# header pill. See THEMES below.
_BASE_TOKENS: dict[str, str] = {
    "ink":       "#0f172a",
    "muted":     "#64748b",
    "bg":        "#f8fafc",
    "surface":   "#ffffff",
    "border":    "#e2e8f0",
    "font":      "-apple-system,Segoe UI,Roboto,sans-serif",
}

# Category themes. Each email template picks one via _render(theme=…) so
# it looks visually distinct at a glance (icon color, CTA color, header
# pill). Add a theme here; templates opt in via their _render() call.
THEMES: dict[str, dict[str, str]] = {
    "action": {  # invite / verify / reset — "please do this"
        "accent":     "#2563eb", "accent_dk":  "#1d4ed8",
        "chip_bg":    "#dbeafe", "chip_fg":    "#1e40af",
        "chip_label": "Action needed",
    },
    "notice": {  # password_changed — informational security confirmation
        "accent":     "#0891b2", "accent_dk":  "#0e7490",
        "chip_bg":    "#cffafe", "chip_fg":    "#155e75",
        "chip_label": "Account notice",
    },
    "alert": {   # pc_unreachable_alert — urgent attention
        "accent":     "#dc2626", "accent_dk":  "#b91c1c",
        "chip_bg":    "#fee2e2", "chip_fg":    "#991b1b",
        "chip_label": "Alert",
    },
    "report": {  # commit_report — post-run summary
        "accent":     "#059669", "accent_dk":  "#047857",
        "chip_bg":    "#d1fae5", "chip_fg":    "#065f46",
        "chip_label": "Report",
    },
}


def _shared_ctx(*, title: str, preheader: str, theme: str) -> dict:
    tokens = {**_BASE_TOKENS, **THEMES.get(theme, THEMES["action"])}
    return {
        "brand":     _brand_name(),
        "support":   _support_line(),
        "c":         tokens,
        "title":     title,
        "preheader": preheader,
    }


def _render(name: str, *, title: str, preheader: str = "", theme: str = "action", **vars) -> tuple[str, str]:
    """Render `NAME.html` + `NAME.txt` with a merged context. `theme` picks
    a color palette (see THEMES). Missing .txt pair raises — every
    template must ship both so recipients on plain-text clients still get
    a legible message."""
    ctx = {**_shared_ctx(title=title, preheader=preheader, theme=theme), **vars}
    html = _env.get_template(f"{name}.html").render(**ctx)
    text = _env.get_template(f"{name}.txt").render(**ctx)
    return html, text


# ---------- send ----------------------------------------------------

def send_mail(to: str, spec: MailSpec) -> tuple[bool, str]:
    """Return (ok, message). Never raises; failures are logged upstream."""
    host = _cfg("SMTP_HOST")
    port = int(_cfg("SMTP_PORT") or "587")
    user = _cfg("SMTP_USER")
    passwd = _cfg("SMTP_PASS")
    email_from = _cfg("EMAIL_FROM") or user
    if not (host and user and passwd and email_from):
        return False, "email skipped: SMTP env vars missing"

    msg = EmailMessage()
    msg["Subject"] = spec.subject
    msg["From"] = email_from
    msg["To"] = to
    msg.set_content(spec.body)
    if spec.html:
        msg.add_alternative(spec.html, subtype="html")
    try:
        with smtplib.SMTP(host, port, timeout=15) as s:
            s.starttls()
            s.login(user, passwd)
            s.send_message(msg)
        return True, f"email sent to {to}"
    except Exception as e:
        return False, f"email failed: {e.__class__.__name__}: {e}"


# ---------- templates ----------------------------------------------
# Thin wrappers that render a Jinja2 template pair and return a MailSpec.
# One function per template so callers stay readable and typed.

_ROLE_LABELS = {"admin": "Administrator", "operator": "Operator", "viewer": "Viewer"}


def invite_email(display_name: str, invite_url: str, invited_by: str, role: str) -> MailSpec:
    subject = f"You've been invited to {_brand_name()}"
    preheader = f"{invited_by} added you as a {_ROLE_LABELS.get(role, role.title())}. Set your password to sign in."
    html, text = _render(
        "invite", theme="action",
        title="Welcome aboard", preheader=preheader,
        display_name=display_name, invite_url=invite_url,
        invited_by=invited_by, role_label=_ROLE_LABELS.get(role, role.title()),
    )
    return MailSpec(subject=subject, body=text, html=html, preheader=preheader)


def verify_email(display_name: str, verify_url: str) -> MailSpec:
    subject = f"Confirm your email · {_brand_name()}"
    preheader = "One-click confirmation to finish setting up your account."
    html, text = _render(
        "verify", theme="action",
        title="Confirm your email", preheader=preheader,
        display_name=display_name, verify_url=verify_url,
    )
    return MailSpec(subject=subject, body=text, html=html, preheader=preheader)


def password_reset_email(display_name: str, reset_url: str) -> MailSpec:
    subject = f"Reset your password · {_brand_name()}"
    preheader = "One-time link. Expires in 30 minutes."
    html, text = _render(
        "password_reset", theme="action",
        title="Reset your password", preheader=preheader,
        display_name=display_name, reset_url=reset_url,
    )
    return MailSpec(subject=subject, body=text, html=html, preheader=preheader)


def password_changed_email(display_name: str, ip: str) -> MailSpec:
    subject = f"Your password was changed · {_brand_name()}"
    preheader = f"Confirmation from {ip}. All sessions have been signed out."
    html, text = _render(
        "password_changed", theme="notice",
        title="Password changed", preheader=preheader,
        display_name=display_name, ip=ip,
    )
    return MailSpec(subject=subject, body=text, html=html, preheader=preheader)


def pc_unreachable_alert_email(
    *, pcs: list[dict], stale_days: int, dashboard_url: str,
) -> MailSpec:
    n = len(pcs)
    subject = f"[ALERT] {n} lab PC{'s' if n != 1 else ''} unreachable for {stale_days}+ days"
    preheader = f"{n} PC{'s' if n != 1 else ''} haven't checked in for {stale_days}+ days."
    html, text = _render(
        "pc_unreachable_alert", theme="alert",
        title="PCs offline", preheader=preheader,
        pcs=pcs, stale_days=stale_days, dashboard_url=dashboard_url,
    )
    return MailSpec(subject=subject, body=text, html=html, preheader=preheader)


def commit_report_email(
    *, run_id: int, counts: dict, started_at: str, ended_at: str,
    files: list[dict], dest_folders: list[str],
) -> MailSpec:
    # Normalize counts so templates don't need `default` filters.
    counts_full = {
        "copied":     int(counts.get("copied", 0)),
        "duplicates": int(counts.get("duplicates", 0)),
        "failed":     int(counts.get("failed", 0)),
        "eligible":   int(counts.get("eligible", 0)),
    }
    status = "FAIL" if counts_full["failed"] else ("OK" if counts_full["copied"] else "EMPTY")
    subject = f"[{status}] Commit #{run_id} · {counts_full['copied']} copied · {datetime.now():%Y-%m-%d %H:%M}"
    preheader = (f"{counts_full['copied']} copied · {counts_full['duplicates']} duplicates "
                 f"· {counts_full['failed']} failed")
    # Failed runs get the alert theme so they stand out in a mailbox skim.
    theme = "alert" if counts_full["failed"] else "report"
    html, text = _render(
        "commit_report", theme=theme,
        title=f"Commit #{run_id}", preheader=preheader,
        run_id=run_id, counts=counts_full,
        started_at=started_at, ended_at=ended_at,
        files=files, dest_folders=dest_folders,
    )
    return MailSpec(subject=subject, body=text, html=html, preheader=preheader)
