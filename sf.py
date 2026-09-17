"""Salesforce JWT-bearer client — reusable auth + REST/SOQL helpers.

Server-to-server (no browser). Reads config from env (see docs/SALESFORCE_INTEGRATION.md):
  SF_LOGIN_URL, SF_CONSUMER_KEY, SF_USERNAME, SF_JWT_KEY_PATH, [SF_JWT_AUDIENCE]

Read-only today (token, SOQL, describe, GET). Upload helpers land in a later milestone.
"""
from __future__ import annotations

import base64
import json
import time
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import jwt  # PyJWT (RS256 needs `cryptography`, already installed)


def soql_str(v: str) -> str:
    """Escape a value for a single-quoted SOQL literal."""
    return v.replace("\\", "\\\\").replace("'", "\\'")


def _need(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        raise RuntimeError(f"{name} not set (check .env / --env-file)")
    return v.strip()


def get_token() -> tuple[str, str]:
    """Return (access_token, instance_url) via the JWT bearer grant."""
    login_url = _need("SF_LOGIN_URL").rstrip("/")
    aud = os.environ.get("SF_JWT_AUDIENCE", login_url).rstrip("/")
    now = int(time.time())
    claims = {
        "iss": _need("SF_CONSUMER_KEY"),
        "sub": _need("SF_USERNAME"),
        "aud": aud,
        "exp": now + 180,
        "iat": now,
    }
    assertion = jwt.encode(claims, Path(_need("SF_JWT_KEY_PATH")).read_bytes(), algorithm="RS256")
    body = urllib.parse.urlencode({
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "assertion": assertion,
    }).encode()
    req = urllib.request.Request(
        f"{login_url}/services/oauth2/token", data=body, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            tok = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"token exchange failed (HTTP {e.code}): {e.read().decode()}") from None
    return tok["access_token"], tok["instance_url"]


class SFClient:
    """Thin authenticated REST client. One token per instance (good for a CLI run)."""

    def __init__(self, version: str | None = None):
        self.token, self.instance = get_token()
        self.version = version or self._latest_version()

    def _req(self, path: str, method: str = "GET", data: dict | None = None) -> dict:
        url = path if path.startswith("http") else f"{self.instance}{path}"
        headers = {"Authorization": f"Bearer {self.token}"}
        raw_body = None
        if data is not None:
            raw_body = json.dumps(data).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=raw_body, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                text = r.read().decode()
                return json.loads(text) if text else {}
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"{method} {url} -> HTTP {e.code}: {e.read().decode()}") from None

    def _latest_version(self) -> str:
        versions = self._req("/services/data/")
        return max(versions, key=lambda v: float(v["version"]))["version"]

    def soql(self, query: str) -> dict:
        return self._req(f"/services/data/v{self.version}/query/?q={urllib.parse.quote(query)}")

    def describe_global(self) -> dict:
        return self._req(f"/services/data/v{self.version}/sobjects/")

    def describe(self, sobject: str) -> dict:
        return self._req(f"/services/data/v{self.version}/sobjects/{sobject}/describe/")

    # ---- domain helpers: the assessment-push path -------------------------

    def account_for_case_number(self, case_number: str) -> dict | None:
        """Case Number (Assignment__c.Name) -> client Person Account.

        A client can have several assignments that all share the case number and
        point at the same Participant, so we collapse to the distinct account(s).
        Returns {account_id, name, is_person_account, assignments, multi_account}
        or None if the case number isn't found.
        """
        recs = self.soql(
            "SELECT Participant__c, Participant__r.Name, Participant__r.IsPersonAccount "
            f"FROM Assignment__c WHERE Name = '{soql_str(case_number)}'"
        ).get("records", [])
        ids = {r["Participant__c"] for r in recs if r.get("Participant__c")}
        if not ids:
            return None
        account_id = next(iter(ids))
        acct = next((r.get("Participant__r") or {}) for r in recs
                    if r.get("Participant__c") == account_id)
        return {
            "account_id": account_id,
            "name": acct.get("Name"),
            "is_person_account": acct.get("IsPersonAccount"),
            "assignments": len(recs),
            "multi_account": len(ids) > 1,
        }

    def list_account_files(self, account_id: str) -> list[dict]:
        """Files attached to a record: [{id, title, ext, size}]."""
        recs = self.soql(
            "SELECT ContentDocumentId, ContentDocument.Title, "
            "ContentDocument.FileExtension, ContentDocument.ContentSize "
            f"FROM ContentDocumentLink WHERE LinkedEntityId = '{soql_str(account_id)}'"
        ).get("records", [])
        out = []
        for r in recs:
            d = r.get("ContentDocument") or {}
            out.append({"id": r.get("ContentDocumentId"), "title": d.get("Title"),
                        "ext": d.get("FileExtension"), "size": d.get("ContentSize")})
        return out

    def upload_file(self, account_id: str, title: str, path_on_client: str, data: bytes) -> str:
        """Create a ContentVersion filed on the account. Returns the ContentVersion Id.

        FirstPublishLocationId auto-creates the ContentDocument + link, so the file
        appears in the record's Files related list in one call.
        """
        res = self._req(
            f"/services/data/v{self.version}/sobjects/ContentVersion",
            method="POST",
            data={
                "Title": title,
                "PathOnClient": path_on_client,
                "FirstPublishLocationId": account_id,
                "VersionData": base64.b64encode(data).decode(),
            },
        )
        return res.get("id")
