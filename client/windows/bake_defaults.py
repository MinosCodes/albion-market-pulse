"""Writes client/windows/defaults.json for a release build.

The release workflow calls this with the repository variable PULSE_SERVER_URL, so the .exe opens
with that server pre-filled. Only the server ADDRESS is baked in, never a key (anyone can download
a release). Fails the build if the address is not a valid https URL.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from albion_flips.liveclient import ClientError, DEFAULT_AODP_UPLOAD, validate_aodp_upload, validate_server_url  # noqa: E402

url = os.environ.get("PULSE_SERVER_URL", "").strip()
aodp = os.environ.get("PULSE_AODP_UPLOAD", "").strip() or DEFAULT_AODP_UPLOAD
try:
    data = {
        "server_url": validate_server_url(url) if url else "",
        "aodp_upload": validate_aodp_upload(aodp),
        "data_client_version": "latest",
    }
except ClientError as e:
    sys.exit(f"Invalid build setting: {e}")
out = Path(__file__).with_name("defaults.json")
out.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
print(f"defaults.json: server_url={data['server_url'] or '(none, asked on first start)'}")
