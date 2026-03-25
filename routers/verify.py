import logging
import asyncio
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Literal
from services.verification_service import get_verification_capabilities, verify_with_azure, verify_with_azure_bytes
from services.liveness_service import get_liveness_session_result, get_session_image_bytes

logger = logging.getLogger("faceauth")
router = APIRouter()


class VerifyRequest(BaseModel):
    source: Literal["live", "upload"] = "live"
    live_image_b64: str | None = None
    uploaded_image_b64: str | None = None
    reference_person_id: str
    tenant_id: str | None = None


class VerifyLiveSessionRequest(BaseModel):
    session_id: str
    reference_person_id: str
    tenant_id: str | None = None


def _is_result_available_status(status: str) -> bool:
    s = str(status or "").strip().lower()
    if "." in s:
        s = s.split(".")[-1]
    s = s.replace("_", "").replace(" ", "")
    return s == "resultavailable"


async def _poll_liveness_result(session_id: str, tenant_id: str | None, retries: int = 5, delay_seconds: float = 0.4) -> dict:
    """Poll Azure liveness result briefly to reduce false inconclusive due to propagation delay."""
    last = None
    for attempt in range(retries):
        last = get_liveness_session_result(session_id, tenant_id=tenant_id)
        decision = str(last.get("decision", "")).strip().lower()
        session_status = str(last.get("sessionStatus", "")).strip()
        if decision == "pass" or _is_result_available_status(session_status):
            return last
        if decision in ("fail",):
            return last
        if attempt < retries - 1:
            await asyncio.sleep(delay_seconds)
    return last or {}


@router.get("/verify/capabilities")
async def verify_capabilities():
    """Return current verification module capabilities and rollout status."""
    return get_verification_capabilities()


@router.post("/verify")
async def verify_identity(req: VerifyRequest):
    """Verify a probe image (live or uploaded) against an enrolled person using Azure Face API."""
    if req.source == "live":
        raise HTTPException(
            status_code=400,
            detail="Direct live image verification is disabled. Use POST /api/verify/live-session after liveness session completion.",
        )
    else:
        probe_image_b64 = req.uploaded_image_b64
        if not probe_image_b64:
            raise HTTPException(status_code=400, detail="uploaded_image_b64 is required when source='upload'.")

    logger.info(
        "Verification request received for person_id='%s' source='%s' tenant_id='%s'",
        req.reference_person_id,
        req.source,
        req.tenant_id,
    )
    result = verify_with_azure(probe_image_b64, req.reference_person_id, req.tenant_id)
    return {
        "status": "ok",
        "source": req.source,
        "tenant_id": req.tenant_id,
        "reference_person_id": req.reference_person_id,
        **result,
    }


@router.post("/verify/live-session")
async def verify_identity_from_live_session(req: VerifyLiveSessionRequest):
    """Run liveness + similarity using the single image captured by the liveness session."""
    if not req.session_id or not req.session_id.strip():
        raise HTTPException(status_code=400, detail="session_id is required.")
    if not req.reference_person_id or not req.reference_person_id.strip():
        raise HTTPException(status_code=400, detail="reference_person_id is required.")

    liveness = await _poll_liveness_result(req.session_id, tenant_id=req.tenant_id)
    liveness_decision = str(liveness.get("decision", "")).strip().lower()
    liveness_is_live = bool(liveness.get("isLive", False))
    liveness_passed = liveness_decision == "pass" and liveness_is_live
    session_image_id = liveness.get("sessionImageId")

    if not liveness_passed:
        return {
            "status": "ok",
            "source": "live",
            "tenant_id": req.tenant_id,
            "reference_person_id": req.reference_person_id,
            "liveness": liveness,
            "matchStatus": "inconclusive",
            "similarityPercent": 0.0,
            "isMatch": False,
            "qualityReason": "Liveness did not pass.",
            "qualityReasons": ["liveness_not_passed"],
            "provider": "azure-face-api",
            "matchBasis": "template_embedding",
            "verificationPerformed": False,
        }

    if not session_image_id:
        raise HTTPException(
            status_code=422,
            detail="Liveness passed but no session image id was provided by Azure.",
        )

    probe_image_bytes = get_session_image_bytes(str(session_image_id))
    verify_result = verify_with_azure_bytes(
        probe_image_bytes,
        req.reference_person_id,
        req.tenant_id,
    )

    return {
        "status": "ok",
        "source": "live",
        "tenant_id": req.tenant_id,
        "reference_person_id": req.reference_person_id,
        "liveness": liveness,
        "verificationPerformed": True,
        **verify_result,
    }
