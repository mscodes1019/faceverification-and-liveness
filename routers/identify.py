import logging
from io import BytesIO
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from config import PERSON_GROUP_ID, VERIFY_PASS_THRESHOLD_PERCENT, VERIFY_INCONCLUSIVE_THRESHOLD_PERCENT, VERIFY_TENANT_THRESHOLDS
from services.face_service import face_client, list_persons
from services.liveness_service import get_liveness_session_result, get_session_image_bytes

logger = logging.getLogger("faceauth")
router = APIRouter()


class IdentifyLiveSessionRequest(BaseModel):
    session_id: str
    tenant_id: str | None = None
    top_k: int = Field(default=5, ge=1, le=20)
    min_similarity_percent: float | None = Field(default=None, ge=0, le=100)


def _resolve_thresholds_percent(tenant_id: str | None) -> tuple[float, float]:
    p = float(VERIFY_PASS_THRESHOLD_PERCENT)
    i = float(VERIFY_INCONCLUSIVE_THRESHOLD_PERCENT)
    if tenant_id:
        cfg = VERIFY_TENANT_THRESHOLDS.get(str(tenant_id))
        if isinstance(cfg, dict):
            p = float(cfg.get("pass", p))
            i = float(cfg.get("inconclusive", i))
    if i > p:
        i = p
    return p, i


def _match_status_from_similarity_percent(similarity_percent: float, pass_threshold: float, inconclusive_threshold: float) -> str:
    if similarity_percent >= pass_threshold:
        return "match"
    if similarity_percent >= inconclusive_threshold:
        return "inconclusive"
    return "no_match"


@router.post("/identify/live-session")
async def identify_from_live_session(req: IdentifyLiveSessionRequest):
    """1:N identification: liveness-gated identify against enrolled person templates."""
    if not req.session_id or not req.session_id.strip():
        raise HTTPException(status_code=400, detail="session_id is required.")

    pass_t, inc_t = _resolve_thresholds_percent(req.tenant_id)
    min_similarity = req.min_similarity_percent if req.min_similarity_percent is not None else inc_t
    min_conf = max(0.0, min(1.0, float(min_similarity) / 100.0))

    liveness = get_liveness_session_result(req.session_id, tenant_id=req.tenant_id)
    liveness_decision = str(liveness.get("decision", "")).strip().lower()
    liveness_is_live = bool(liveness.get("isLive", False))
    liveness_passed = liveness_decision == "pass" and liveness_is_live

    if not liveness_passed:
        return {
            "status": "ok",
            "mode": "identify_1_to_n",
            "tenant_id": req.tenant_id,
            "session_id": req.session_id,
            "liveness": liveness,
            "identificationPerformed": False,
            "candidates": [],
            "matchStatus": "inconclusive",
            "thresholds": {
                "pass": round(pass_t, 2),
                "inconclusive": round(inc_t, 2),
            },
            "provider": "azure-face-api",
            "reason": "liveness_not_passed",
        }

    session_image_id = liveness.get("sessionImageId")
    if not session_image_id:
        raise HTTPException(status_code=422, detail="Liveness passed but no session image id is available for identify.")

    image_bytes = get_session_image_bytes(str(session_image_id))

    try:
        detected = face_client.face.detect_with_stream(
            image=BytesIO(image_bytes),
            detection_model="detection_03",
            recognition_model="recognition_04",
            return_face_id=True,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Face detection failed for identify: {e}")

    if len(detected) == 0:
        return {
            "status": "ok",
            "mode": "identify_1_to_n",
            "tenant_id": req.tenant_id,
            "session_id": req.session_id,
            "liveness": liveness,
            "identificationPerformed": False,
            "candidates": [],
            "matchStatus": "inconclusive",
            "thresholds": {
                "pass": round(pass_t, 2),
                "inconclusive": round(inc_t, 2),
            },
            "provider": "azure-face-api",
            "reason": "no_face_detected",
        }

    if len(detected) > 1:
        return {
            "status": "ok",
            "mode": "identify_1_to_n",
            "tenant_id": req.tenant_id,
            "session_id": req.session_id,
            "liveness": liveness,
            "identificationPerformed": False,
            "candidates": [],
            "matchStatus": "inconclusive",
            "thresholds": {
                "pass": round(pass_t, 2),
                "inconclusive": round(inc_t, 2),
            },
            "provider": "azure-face-api",
            "reason": "multiple_faces_detected",
        }

    face_id = getattr(detected[0], "face_id", None)
    if not face_id:
        raise HTTPException(status_code=500, detail="Face detection did not return a face_id for identify.")

    try:
        identify_result = face_client.face.identify(
            face_ids=[face_id],
            large_person_group_id=PERSON_GROUP_ID,
            max_num_of_candidates_returned=req.top_k,
            confidence_threshold=min_conf,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Identify call failed: {e}")

    face_candidates = []
    if identify_result and len(identify_result) > 0:
        face_candidates = getattr(identify_result[0], "candidates", None) or []

    people = list_persons()
    user_index = {str(p.get("person_id")): p for p in people}

    candidates = []
    for rank, c in enumerate(face_candidates, start=1):
        pid = str(getattr(c, "person_id", "") or "")
        conf = float(getattr(c, "confidence", 0.0) or 0.0)
        sim = round(conf * 100.0, 2)
        profile = user_index.get(pid, {})
        candidates.append(
            {
                "rank": rank,
                "personId": pid,
                "userId": profile.get("user_id"),
                "name": profile.get("name"),
                "similarityPercent": sim,
            }
        )

    top_similarity = candidates[0]["similarityPercent"] if candidates else 0.0
    match_status = _match_status_from_similarity_percent(top_similarity, pass_t, inc_t)

    return {
        "status": "ok",
        "mode": "identify_1_to_n",
        "tenant_id": req.tenant_id,
        "session_id": req.session_id,
        "liveness": liveness,
        "identificationPerformed": True,
        "candidates": candidates,
        "matchStatus": match_status,
        "thresholds": {
            "pass": round(pass_t, 2),
            "inconclusive": round(inc_t, 2),
        },
        "provider": "azure-face-api",
    }
