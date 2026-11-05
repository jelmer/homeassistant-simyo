"""Constants for the Simyo integration."""

from datetime import timedelta

DOMAIN = "simyo"

CONF_PHONE_NUMBER = "phone_number"
CONF_DEVICE_ID = "device_id"
CONF_SESSION_TOKEN = "session_token"
CONF_VERIFICATION_CODE = "verification_code"
CONF_AUTO_REAUTH_BLOCKED = "auto_reauth_blocked"

DEFAULT_SCAN_INTERVAL = timedelta(hours=5)

API_BASE = "https://appapi.simyo.nl/webapi/api"
CONF_AUTH_TRANSPORT = "auth_transport"
AUTH_TRANSPORT = "app_v1"
# Public app client identifier embedded in Android 4.31.2, not an account secret.
CLIENT_TOKEN = "e77b7e2f43db41bb95b17a2a11581a38"

CLIENT_PLATFORM = "android"
CLIENT_VERSION = "4.31.2"
USER_AGENT = f"MijnSimyoFT/{CLIENT_VERSION} (Android)"
