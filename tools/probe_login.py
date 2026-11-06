"""Show the Simyo login response structure without credentials or token values.

Run locally in an interactive terminal. Uses only Python's standard library.
Does not submit an OTP, bypass MFA, or save the login response.
"""

from __future__ import annotations

import getpass
import json
import runpy
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

KNOWN_MFA_STATUSES = {"ACTIVE", "PENDING_VERIFICATION", "DISABLED_BY_CUSTOMER"}


def response_shape(value, key="", depth=0):
    """Never print arbitrary response values, even inside lists or errors."""
    if depth > 8:
        return "<depth limit>"
    if isinstance(value, dict):
        return {
            str(k): response_shape(v, str(k), depth + 1)
            for k, v in value.items()
            if len(str(k)) < 80 and not any(char.isdigit() for char in str(k))
        }
    if isinstance(value, list):
        return [response_shape(v, depth=depth + 1) for v in value[:3]]
    if key == "mfaStatus" and isinstance(value, str) and value in KNOWN_MFA_STATUSES:
        return value
    return f"<{type(value).__name__}: redacted>"


def main():
    if not sys.stdin.isatty():
        raise SystemExit(
            "Run this script in an interactive terminal; do not pipe credentials."
        )
    constants = runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "custom_components/simyo/const.py")
    )
    print("Signs in once to the official Simyo app API. No OTP will be submitted.")
    phone = input("Simyo phone number (06xxxxxxxx): ").strip()
    if (
        len(phone) != 10
        or not phone.startswith("06")
        or not phone.isascii()
        or not phone.isdigit()
    ):
        raise SystemExit("Enter a Dutch number as 06xxxxxxxx.")
    password = getpass.getpass("Mijn Simyo password (hidden): ")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-Client-Token": constants["CLIENT_TOKEN"],
        "X-Client-Platform": constants["CLIENT_PLATFORM"],
        "X-Client-Version": constants["CLIENT_VERSION"],
        "User-Agent": constants["USER_AGENT"],
        "X-Device-ID": str(uuid4()),
    }
    request = Request(
        constants["API_BASE"] + "/v1/sessions",
        data=json.dumps({"phoneNumber": phone, "password": password}).encode(),
        headers=headers,
        method="POST",
    )
    try:
        try:
            response = urlopen(request, timeout=30)
        except HTTPError as error:
            response = error
        with response:
            status = response.code
            raw = response.read(1024 * 1024 + 1)
        print("HTTP status:", status)
        if len(raw) > 1024 * 1024:
            print("Response too large; contents omitted.")
            return
        try:
            shape = response_shape(json.loads(raw))
        except (ValueError, UnicodeError):
            print("Non-JSON response; contents omitted.")
            return
        print("REDACTED RESPONSE STRUCTURE (no password, code or token values):")
        print(json.dumps(shape, indent=2))
    except (URLError, TimeoutError, OSError):
        print(
            "Transport failure. Details omitted to avoid exposing account information."
        )


if __name__ == "__main__":
    main()
