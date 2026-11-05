"""Local app-session cache; no passwords, codes or API payloads are saved."""

import hashlib
import json
import os
import tempfile
from pathlib import Path


def account_key(phone):
    return hashlib.sha256(phone.strip().encode()).hexdigest()


def load_session(path, phone):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("account") != account_key(phone):
        return None
    if not all(
        isinstance(data.get(k), str) and data[k] for k in ("device_id", "session_token")
    ):
        return None
    return data


def save_session(path, phone, client):
    if not client.session_token:
        return
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".session-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(
                {
                    "account": account_key(phone),
                    "device_id": client.device_id,
                    "session_token": client.session_token,
                },
                stream,
            )
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def clear_session(path):
    Path(path).unlink(missing_ok=True)
