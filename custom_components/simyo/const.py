"""Constants for the Simyo integration."""

from datetime import timedelta

DOMAIN = "simyo"

CONF_PHONE_NUMBER = "phone_number"
CONF_DEVICE_ID = "device_id"
CONF_SESSION_TOKEN = "session_token"
CONF_WEB_SESSION = "web_session"
CONF_VERIFICATION_CODE = "verification_code"
CONF_AUTO_REAUTH_BLOCKED = "auto_reauth_blocked"

DEFAULT_SCAN_INTERVAL = timedelta(hours=5)

API_BASE = "https://appapi.simyo.nl/simyoapi/api/v1"
WEB_BASE = "https://mijn.simyo.nl"

CLIENT_PLATFORM = "ios"
CLIENT_VERSION = "4.31.1"
USER_AGENT = f"MijnSimyoFT/{CLIENT_VERSION} (iOS 26.3; iPhone16,1)"
