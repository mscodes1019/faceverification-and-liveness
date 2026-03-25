import logging
import base64
from fastapi import APIRouter, HTTPException
from fastapi import UploadFile, File, Form
from pydantic import BaseModel
from services.liveness_service import (
    create_liveness_session,
    get_liveness_session_result,
    create_liveness_with_verify_session,
    get_liveness_with_verify_session_result,
    create_liveness_verify_session_for_person,
)
from config import VERIFY_PASS_THRESHOLD_PERCENT, VERIFY_INCONCLUSIVE_THRESHOLD_PERCENT

logger = logging.getLogger("faceauth")
router = APIRouter()


class CreateLivenessSessionRequest(BaseModel):
    livenessOperationMode: str = "PassiveActive"


class CreateLivenessWithVerifySessionRequest(BaseModel):
    verify_image_b64: str
    livenessOperationMode: str = "PassiveActive"


class CreateLivenessVerifySessionForPersonRequest(BaseModel):
    person_id: str
    livenessOperationMode: str = "PassiveActive"


class LivenessCheckRequest(BaseModel):
    session_id: str
    tenant_id: str | None = None
    tenant_threshold: float | None = None
    with_verify: bool = False


@router.post("/liveness/session")
async def create_session(req: CreateLivenessSessionRequest = CreateLivenessSessionRequest()):
    """Create Azure liveness session and return session id + auth token."""
    try:
        return create_liveness_session(req.livenessOperationMode)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Create liveness session failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/liveness/session/{session_id}/result")
async def get_session_result(
    session_id: str,
    tenant_id: str | None = None,
    tenant_threshold: float | None = None,
):
    """Get Azure liveness session result mapped to AC-friendly output."""
    try:
        return get_liveness_session_result(session_id, tenant_id, tenant_threshold)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get liveness session result failed for session_id='{session_id}': {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/liveness/verify-session")
async def create_verify_session(req: CreateLivenessWithVerifySessionRequest):
    """Create Azure liveness+verify session with reference verify image."""
    try:
        return create_liveness_with_verify_session(req.verify_image_b64, req.livenessOperationMode)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Create liveness+verify session failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/liveness/verify-session-for-person")
async def create_verify_session_for_person(req: CreateLivenessVerifySessionForPersonRequest):
    """Create Azure liveness+verify session using the enrolled reference image for person_id.

    This is the preferred liveness flow. Azure runs the challenge and similarity together
    using the liveness-guaranteed capture frame — no separate selfie step needed.
    """
    try:
        return create_liveness_verify_session_for_person(req.person_id, req.livenessOperationMode)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Create liveness+verify session for person failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def _enrich_with_similarity(result: dict, tenant_id: str | None) -> dict:
    """Convert verifyMatchConfidence (0-1) → similarityPercent + matchStatus."""
    vmc = result.get("verifyMatchConfidence")
    if vmc is None:
        return result
    similarity_percent = round(float(vmc) * 100, 2)
    pass_t = float(VERIFY_PASS_THRESHOLD_PERCENT)
    inc_t  = float(VERIFY_INCONCLUSIVE_THRESHOLD_PERCENT)
    if similarity_percent >= pass_t:
        match_status = "pass"
    elif similarity_percent >= inc_t:
        match_status = "inconclusive"
    else:
        match_status = "fail"
    result["similarityPercent"]   = similarity_percent
    result["matchStatus"]         = match_status
    result["verificationPerformed"] = True
    result["thresholds"]          = {"pass": pass_t, "inconclusive": inc_t}
    return result


@router.get("/liveness/verify-session/{session_id}/result")
async def get_verify_session_result(
    session_id: str,
    tenant_id: str | None = None,
    tenant_threshold: float | None = None,
):
    """Get Azure liveness+verify session result with liveness decision + similarity fields."""
    try:
        result = get_liveness_with_verify_session_result(session_id, tenant_id, tenant_threshold)
        return _enrich_with_similarity(result, tenant_id)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get liveness+verify session result failed for session_id='{session_id}': {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/liveness/check")
async def check_liveness(req: LivenessCheckRequest):
    """
    One-shot liveness check result endpoint for verification workflows.
    Clients submit session_id after capture is complete and receive AC-style decision payload.
    """
    try:
        if req.with_verify:
            return get_liveness_with_verify_session_result(req.session_id, req.tenant_id, req.tenant_threshold)
        return get_liveness_session_result(req.session_id, req.tenant_id, req.tenant_threshold)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Liveness check failed for session_id='{req.session_id}': {e}")
        raise HTTPException(status_code=500, detail=str(e))


# Legacy compatibility endpoints to support clients originally built on
# Face_Liveleness/ObjectValidation/flask livenessdetector.py
@router.post("/create_liveness_session")
async def legacy_create_session(req: CreateLivenessSessionRequest = CreateLivenessSessionRequest()):
    try:
        return create_liveness_session(req.livenessOperationMode)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Legacy create liveness session failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/get_liveness_session_result/{session_id}")
async def legacy_get_session_result(
    session_id: str,
    tenant_id: str | None = None,
    tenant_threshold: float | None = None,
):
    try:
        return get_liveness_session_result(session_id, tenant_id, tenant_threshold)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Legacy get liveness session result failed for session_id='{session_id}': {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/create_liveness_with_verify_session")
async def legacy_create_verify_session(
    verify_image: UploadFile = File(...),
    livenessOperationMode: str = Form("PassiveActive"),
):
    try:
        image_bytes = await verify_image.read()
        verify_image_b64 = base64.b64encode(image_bytes).decode("ascii")
        return create_liveness_with_verify_session(verify_image_b64, livenessOperationMode)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Legacy create liveness+verify session failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/get_liveness_with_verify_session_result/{session_id}")
async def legacy_get_verify_session_result(
    session_id: str,
    tenant_id: str | None = None,
    tenant_threshold: float | None = None,
):
    try:
        return get_liveness_with_verify_session_result(session_id, tenant_id, tenant_threshold)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Legacy get liveness+verify session result failed for session_id='{session_id}': {e}")
        raise HTTPException(status_code=500, detail=str(e))
