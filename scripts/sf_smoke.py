"""Salesforce JWT-bearer connection smoke test (read-only).

Mints a signed JWT from the local private key, exchanges it for an access token
at the org's token endpoint, then calls /userinfo to confirm who we authenticated
as. No writes — safe against any org.

Run:
  uv run --env-file .env python scripts/sf_smoke.py

Env (from .env):
  SF_LOGIN_URL     https://test.salesforce.com (sandbox) | https://login.salesforce.com (prod)
  SF_CONSUMER_KEY  External Client App Consumer Key   (JWT iss)
  SF_USERNAME      integration user, sandbox form      (JWT sub)
  SF_JWT_KEY_PATH  path to the RS256 private key (.key)
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import jwt  # PyJWT

# Windows consoles default to cp1252 and choke on → / ✓. Force UTF-8 output.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


def _need(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        sys.exit(f"[config] {name} is not set (check .env / --env-file).")
    return v.strip()


def _post_form(url: str, data: dict[str, str]) -> tuple[int, dict]:
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {"error": "http_error", "error_description": str(e)}


def _get_json(url: str, token: str) -> tuple[int, dict]:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {"error": str(e)}


def main() -> None:
    login_url = _need("SF_LOGIN_URL").rstrip("/")
    consumer_key = _need("SF_CONSUMER_KEY")
    username = _need("SF_USERNAME")
    key_path = Path(_need("SF_JWT_KEY_PATH"))
    # aud override lets us retry with the My Domain URL if test.salesforce.com is rejected
    aud = os.environ.get("SF_JWT_AUDIENCE", login_url).rstrip("/")

    if not key_path.is_file():
        sys.exit(f"[config] private key not found at {key_path}")
    private_key = key_path.read_bytes()

    print("Config:")
    print(f"  login_url : {login_url}")
    print(f"  aud       : {aud}")
    print(f"  iss (key) : {consumer_key[:8]}… (len {len(consumer_key)})")
    print(f"  sub (user): {username}")
    print(f"  key       : {key_path} ({key_path.stat().st_size} bytes)\n")

    # ---- 1. mint the assertion ----
    now = int(time.time())
    claims = {"iss": consumer_key, "sub": username, "aud": aud, "exp": now + 180, "iat": now}
    try:
        assertion = jwt.encode(claims, private_key, algorithm="RS256")
    except Exception as e:
        sys.exit(f"[jwt] failed to sign assertion: {e.__class__.__name__}: {e}")

    # ---- 2. exchange for an access token ----
    print("→ Exchanging JWT for an access token…")
    status, tok = _post_form(f"{login_url}/services/oauth2/token", {
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "assertion": assertion,
    })

    if status != 200 or "access_token" not in tok:
        err = tok.get("error", "unknown")
        desc = tok.get("error_description", "")
        print(f"\n✗ Token exchange FAILED (HTTP {status}): {err} — {desc}\n")
        hints = {
            "invalid_client_id": "App still propagating (wait 2–10 min) or Consumer Key wrong.",
            "invalid_grant": "sub user not pre-authorized, or username isn't the sandbox "
                             "(…@…com.itsupport) form, or the uploaded cert doesn't match this key.",
            "invalid_app_access": "User isn't assigned to the app (App Policies → Select Profiles).",
        }
        if err in hints:
            print(f"  hint: {hints[err]}")
        if err == "invalid_grant" and "audience" in desc.lower():
            mydomain = login_url.replace("test.salesforce.com", "").strip("/")
            print("  hint: audience rejected — retry with the My Domain URL by setting "
                  "SF_JWT_AUDIENCE=https://<your-mydomain>.my.salesforce.com")
        sys.exit(1)

    print("✓ Access token obtained.")
    instance_url = tok.get("instance_url", "")
    print(f"  instance_url : {instance_url}")
    print(f"  token_type   : {tok.get('token_type')}")
    print(f"  scope        : {tok.get('scope')}\n")

    # ---- 3. confirm identity (read-only) ----
    print("→ Calling /services/oauth2/userinfo …")
    s2, info = _get_json(f"{instance_url}/services/oauth2/userinfo", tok["access_token"])
    if s2 == 200:
        print("✓ Authenticated as:")
        print(f"  name         : {info.get('name')}")
        print(f"  username     : {info.get('preferred_username')}")
        print(f"  email        : {info.get('email')}")
        print(f"  org id       : {info.get('organization_id')}")
        print(f"  user id      : {info.get('user_id')}")
        print("\n✅ Salesforce JWT connection is GOOD.")
    else:
        print(f"  (token worked, but userinfo returned HTTP {s2}: {info})")
        print("\n✅ Token exchange succeeded — connection is GOOD (userinfo optional).")


if __name__ == "__main__":
    main()
