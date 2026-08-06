"""Smallest thing that proves SMB works: connect + list C$ root."""
import os
import sys
import smbclient

HOST = "192.168.72.172"
USER = os.environ.get("SMB_USER")
PASS = os.environ.get("SMB_PASS")

if not USER or not PASS:
    sys.exit("Set SMB_USER and SMB_PASS env vars first.")

smbclient.register_session(HOST, username=USER, password=PASS)

print(f"Connected to {HOST}. Listing C$ root:\n")
for entry in smbclient.scandir(rf"\\{HOST}\C$"):
    kind = "DIR " if entry.is_dir() else "FILE"
    print(f"  {kind}  {entry.name}")
