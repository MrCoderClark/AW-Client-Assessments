"""Generate an Ed25519 signing key for JWT access tokens.

Run:  uv run python scripts/gen_jwt_key.py

Writes secrets/jwt_current.pem (private) + secrets/jwt_current.pub (public)
and prints the env vars to add to .env. Rotation: rerun with --kid <name>
and set the new files as current; keep the old public key around for the
overlap window (M6 wires the JWKS multi-key path).
"""
from __future__ import annotations

import argparse
import secrets as _secrets
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kid", default=None,
                    help="key id (default: k-YYYYMM-<4 hex chars>)")
    ap.add_argument("--dir", default="secrets",
                    help="output directory (default: secrets/)")
    args = ap.parse_args()

    from datetime import UTC, datetime
    kid = args.kid or f"k-{datetime.now(UTC):%Y%m}-{_secrets.token_hex(2)}"

    outdir = Path(args.dir)
    outdir.mkdir(exist_ok=True)
    priv_path = outdir / f"jwt_{kid}.pem"
    pub_path = outdir / f"jwt_{kid}.pub"

    if priv_path.exists() or pub_path.exists():
        sys.exit(f"refusing to overwrite existing {priv_path} / {pub_path}")

    pk = Ed25519PrivateKey.generate()
    priv_pem = pk.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_pem = pk.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    priv_path.write_bytes(priv_pem)
    pub_path.write_bytes(pub_pem)
    # tighten perms on the private key (POSIX only; no-op on Windows filesystems)
    try:
        priv_path.chmod(0o600)
    except OSError:
        pass

    refresh_secret = _secrets.token_hex(32)

    print(f"[new] wrote {priv_path}")
    print(f"[new] wrote {pub_path}")
    print()
    print("Add to .env:")
    print(f'AUTH_JWT_KID="{kid}"')
    print(f'AUTH_JWT_PRIVATE_KEY_PATH="{priv_path.as_posix()}"')
    print(f'AUTH_JWT_PUBLIC_KEY_PATH="{pub_path.as_posix()}"')
    print(f'AUTH_REFRESH_HASH_SECRET="{refresh_secret}"')


if __name__ == "__main__":
    main()
