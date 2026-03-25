import logging
from datetime import datetime, timezone
from fastapi import APIRouter
from config import PERSON_GROUP_ID, CONTAINER_NAME
from services.face_service import health_check as face_health
from services.blob_service import health_check as blob_health

logger = logging.getLogger("faceauth")
router = APIRouter()


@router.get("/health")
async def health():
    """Check connectivity to Face API and Blob Storage."""
    results = {}

    try:
        face_health()
        results["face_api"]           = "connected"
        results["large_person_group"] = PERSON_GROUP_ID
    except Exception as e:
        results["face_api"] = f"error: {str(e)}"

    try:
        blob_health()
        results["blob_storage"] = "connected"
        results["container"]    = CONTAINER_NAME
    except Exception as e:
        results["blob_storage"] = f"error: {str(e)}"

    results["timestamp"] = datetime.now(timezone.utc).isoformat()
    return results