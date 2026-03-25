import os
import json
from dotenv import load_dotenv

# Ensure project .env values take precedence over any stale shell/user env vars.
load_dotenv(override=True)

# Azure Face API
FACE_KEY              = os.getenv("FACE_API_KEY") or os.getenv("API_KEY")
FACE_ENDPOINT         = os.getenv("FACE_ENDPOINT_URL") or os.getenv("ENDPOINT")

# Azure Face Liveness (falls back to Face API credentials if not provided separately)
LIVENESS_ENDPOINT      = os.getenv("LIVENESS_ENDPOINT", FACE_ENDPOINT)
LIVENESS_API_KEY       = os.getenv("LIVENESS_API_KEY", FACE_KEY)
LIVENESS_THRESHOLD     = float(os.getenv("LIVENESS_THRESHOLD", "0.70"))

# Optional tenant-level liveness threshold overrides as JSON string, for example:
# {"tenantA":0.7,"tenantB":0.8}
_liveness_thresholds_raw = os.getenv("LIVENESS_TENANT_THRESHOLDS", "{}")
try:
    _liveness_thresholds_json = json.loads(_liveness_thresholds_raw)
except Exception:
    _liveness_thresholds_json = {}

LIVENESS_TENANT_THRESHOLDS = {}
if isinstance(_liveness_thresholds_json, dict):
    for tenant_id, value in _liveness_thresholds_json.items():
        try:
            t = float(value)
        except Exception:
            continue
        LIVENESS_TENANT_THRESHOLDS[str(tenant_id)] = max(0.0, min(1.0, t))

# Azure Blob Storage
AZURE_STORAGE_CONNECTION = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
AZURE_STORAGE_ACCOUNT_URL = os.getenv("AZURE_STORAGE_ACCOUNT_URL")
AZURE_STORAGE_AUTH_MODE   = os.getenv("AZURE_STORAGE_AUTH_MODE", "connection_string")
CONTAINER_NAME           = os.getenv("BLOB_CONTAINER_NAME", "msfacecontainer")
BLOB_PREFIX              = os.getenv("BLOB_PREFIX", "face-enrollment-images")

# Large Person Group
PERSON_GROUP_ID       = os.getenv("PERSON_GROUP_ID", "faceauth_large_group")

# Limits
MAX_IMAGE_MB          = 4
MAX_IMAGE_BYTES       = MAX_IMAGE_MB * 1024 * 1024

def _to_percent(raw_value: str, default_percent: float) -> float:
    """
    Parse threshold from env as either ratio (0-1) or percent (0-100).
    Examples: 0.85 -> 85.0, 85 -> 85.0
    """
    try:
        v = float(raw_value)
    except Exception:
        return default_percent

    if v <= 1.0:
        v = v * 100.0
    return max(0.0, min(100.0, v))


# Verification thresholds (stored as percent 0-100)
VERIFY_PASS_THRESHOLD_PERCENT = _to_percent(os.getenv("VERIFY_PASS_THRESHOLD", "85"), 85.0)
VERIFY_INCONCLUSIVE_THRESHOLD_PERCENT = _to_percent(
    os.getenv("VERIFY_INCONCLUSIVE_THRESHOLD", "75"),
    75.0,
)

# Backward-compatible ratio values for existing code paths.
VERIFY_PASS_THRESHOLD = VERIFY_PASS_THRESHOLD_PERCENT / 100.0
VERIFY_INCONCLUSIVE_THRESHOLD = VERIFY_INCONCLUSIVE_THRESHOLD_PERCENT / 100.0

# Optional tenant-level threshold overrides as JSON string, for example:
# {"tenantA":{"pass":85,"inconclusive":75},"tenantB":{"pass":90,"inconclusive":80}}
_tenant_thresholds_raw = os.getenv("VERIFY_TENANT_THRESHOLDS", "{}")
try:
    _tenant_thresholds_json = json.loads(_tenant_thresholds_raw)
except Exception:
    _tenant_thresholds_json = {}

VERIFY_TENANT_THRESHOLDS = {}
if isinstance(_tenant_thresholds_json, dict):
    for tenant_id, cfg in _tenant_thresholds_json.items():
        if not isinstance(cfg, dict):
            continue
        p = _to_percent(str(cfg.get("pass", VERIFY_PASS_THRESHOLD_PERCENT)), VERIFY_PASS_THRESHOLD_PERCENT)
        i = _to_percent(
            str(cfg.get("inconclusive", VERIFY_INCONCLUSIVE_THRESHOLD_PERCENT)),
            VERIFY_INCONCLUSIVE_THRESHOLD_PERCENT,
        )
        VERIFY_TENANT_THRESHOLDS[str(tenant_id)] = {
            "pass": p,
            "inconclusive": i,
        }

# CORS — change to your domain in production
ALLOWED_ORIGINS       = [
     "http://localhost:8000",
     "http://127.0.0.1:8000",
     "http://localhost:8001",
     "http://127.0.0.1:8001",
]