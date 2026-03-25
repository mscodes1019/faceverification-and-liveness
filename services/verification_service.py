import base64
import hashlib
from datetime import datetime, timezone
from io import BytesIO
from fastapi import HTTPException
from config import (
    PERSON_GROUP_ID,
    MAX_IMAGE_BYTES,
    MAX_IMAGE_MB,
    VERIFY_PASS_THRESHOLD_PERCENT,
    VERIFY_INCONCLUSIVE_THRESHOLD_PERCENT,
    VERIFY_TENANT_THRESHOLDS,
)
from services.face_service import (
    face_client,
    is_face_masked,
    is_face_wearing_glasses,
    is_face_occluded,
)


# Demo-time in-memory guard against verifying with the exact enrollment image.
_ENROLLMENT_IMAGE_HASH_BY_PERSON: dict[str, str] = {}


def decode_image(image_b64: str) -> bytes:
    """Decode a base64 image string into bytes."""
    try:
        data = image_b64.split(",")[1] if "," in image_b64 else image_b64
        return base64.b64decode(data)
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Invalid image format. Must be base64 encoded.",
        )


def _resolve_thresholds_percent(tenant_id: str | None) -> tuple[float, float]:
    pass_threshold = VERIFY_PASS_THRESHOLD_PERCENT
    inconclusive_threshold = VERIFY_INCONCLUSIVE_THRESHOLD_PERCENT

    if tenant_id:
        cfg = VERIFY_TENANT_THRESHOLDS.get(str(tenant_id))
        if isinstance(cfg, dict):
            pass_threshold = float(cfg.get("pass", pass_threshold))
            inconclusive_threshold = float(cfg.get("inconclusive", inconclusive_threshold))

    # Ensure a sane ordering.
    if inconclusive_threshold > pass_threshold:
        inconclusive_threshold = pass_threshold

    return pass_threshold, inconclusive_threshold


def _match_status_from_similarity_percent(similarity_percent: float, pass_threshold: float, inconclusive_threshold: float) -> str:
    if similarity_percent >= pass_threshold:
        return "pass"
    if similarity_percent >= inconclusive_threshold:
        return "inconclusive"
    return "fail"


def _quality_reasons_from_face(face) -> list[str]:
    reasons: list[str] = []

    if is_face_masked(face) is True:
        reasons.append("mask_detected")
    if is_face_wearing_glasses(face) is True:
        reasons.append("glasses_detected")
    if is_face_occluded(face) is True:
        reasons.append("face_occluded")

    attrs = getattr(face, "face_attributes", None)
    if attrs is not None:
        quality = getattr(attrs, "quality_for_recognition", None)
        if quality is not None and str(quality).strip().lower() == "low":
            # Azure quality-for-recognition low is treated as inconclusive input quality.
            reasons.append("low_sharpness")

    return reasons


def register_enrollment_image(person_id: str, image_bytes: bytes):
    """Store hash of enrollment image so verify can require a fresh live sample."""
    if not person_id or not image_bytes:
        return
    _ENROLLMENT_IMAGE_HASH_BY_PERSON[person_id] = hashlib.sha256(image_bytes).hexdigest()


def verify_with_azure(live_image_b64: str, reference_person_id: str, tenant_id: str | None = None) -> dict:
    image_bytes = decode_image(live_image_b64)
    return verify_with_azure_bytes(image_bytes, reference_person_id, tenant_id)


def verify_with_azure_bytes(image_bytes: bytes, reference_person_id: str, tenant_id: str | None = None) -> dict:
    """
    Verify image bytes against an enrolled person using Azure Face API.
    Returns confidence-backed decision metadata.
    """
    if not reference_person_id or not reference_person_id.strip():
        raise HTTPException(status_code=400, detail="reference_person_id is required for verification.")

    pass_threshold, inconclusive_threshold = _resolve_thresholds_percent(tenant_id)

    incoming_hash = hashlib.sha256(image_bytes).hexdigest()
    enrolled_hash = _ENROLLMENT_IMAGE_HASH_BY_PERSON.get(reference_person_id)
    if enrolled_hash and incoming_hash == enrolled_hash:
        return {
            "matchStatus": "inconclusive",
            "similarityPercent": 0.0,
            "isMatch": False,
            "qualityReason": "Verification requires a fresh live image, not the same enrollment photo.",
            "qualityReasons": ["same_image_replay"],
            "thresholds": {
                "pass": round(pass_threshold, 2),
                "inconclusive": round(inconclusive_threshold, 2),
            },
            "provider": "azure-face-api",
            "matchBasis": "template_embedding",
        }

    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"Image too large. Maximum allowed size is {MAX_IMAGE_MB}MB.",
        )

    try:
        detected = face_client.face.detect_with_stream(
            image=BytesIO(image_bytes),
            detection_model="detection_03",
            recognition_model="recognition_04",
            return_face_attributes=["mask", "glasses", "occlusion"],
            return_face_id=True,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Face detection failed during verification: {str(e)}")

    if len(detected) == 0:
        return {
            "matchStatus": "inconclusive",
            "similarityPercent": 0.0,
            "isMatch": False,
            "qualityReason": "No face detected in probe image.",
            "qualityReasons": ["no_face_detected"],
            "thresholds": {
                "pass": round(pass_threshold, 2),
                "inconclusive": round(inconclusive_threshold, 2),
            },
            "provider": "azure-face-api",
            "matchBasis": "template_embedding",
        }
    if len(detected) > 1:
        return {
            "matchStatus": "inconclusive",
            "similarityPercent": 0.0,
            "isMatch": False,
            "qualityReason": "Multiple faces detected in probe image.",
            "qualityReasons": ["multiple_faces_detected"],
            "thresholds": {
                "pass": round(pass_threshold, 2),
                "inconclusive": round(inconclusive_threshold, 2),
            },
            "provider": "azure-face-api",
            "matchBasis": "template_embedding",
        }

    face = detected[0]
    quality_reasons = _quality_reasons_from_face(face)
    if quality_reasons:
        blocked_reason = "Poor quality input for verification."
        return {
            "matchStatus": "inconclusive",
            "similarityPercent": 0.0,
            "isMatch": False,
            "qualityReason": blocked_reason,
            "qualityReasons": quality_reasons,
            "thresholds": {
                "pass": round(pass_threshold, 2),
                "inconclusive": round(inconclusive_threshold, 2),
            },
            "provider": "azure-face-api",
            "matchBasis": "template_embedding",
        }

    try:
        verify_result = face_client.face.verify_face_to_person(
            face_id=face.face_id,
            person_id=reference_person_id,
            large_person_group_id=PERSON_GROUP_ID,
        )
    except Exception as e:
        msg = str(e)
        if "PersonGroupNotTrained" in msg or "LargePersonGroupNotTrained" in msg or "Training" in msg:
            raise HTTPException(
                status_code=409,
                detail="Verification model is not trained yet. Please run POST /api/train after enrollment, then retry verification.",
            )
        if "PersonNotFound" in msg or "LargePersonGroupPersonNotFound" in msg:
            raise HTTPException(
                status_code=404,
                detail="Enrolled person was not found in the person group. Re-enroll or verify with the correct person_id.",
            )
        raise HTTPException(status_code=500, detail=f"Verification call failed: {msg}")

    confidence = float(getattr(verify_result, "confidence", 0.0) or 0.0)
    is_identical = bool(getattr(verify_result, "is_identical", False))

    similarity_percent = round(confidence * 100, 2)
    match_status = _match_status_from_similarity_percent(similarity_percent, pass_threshold, inconclusive_threshold)

    # If Azure says non-identical but confidence falls in pass band, cap to inconclusive.
    if match_status == "pass" and not is_identical:
        match_status = "inconclusive"

    return {
        "matchStatus": match_status,
        "similarityPercent": similarity_percent,
        "isMatch": is_identical and match_status == "pass",
        "qualityReason": None,
        "qualityReasons": [],
        "thresholds": {
            "pass": round(pass_threshold, 2),
            "inconclusive": round(inconclusive_threshold, 2),
        },
        "provider": "azure-face-api",
        "matchBasis": "template_embedding",
    }


def get_verification_capabilities() -> dict:
    """Describe current verification module status and capabilities."""
    return {
        "module": "verification",
        "status": "active",
        "supports": {
            "similarity_score": True,
            "threshold_decisioning": True,
            "template_based_matching": True,
            "quality_reasons": True,
            "live_probe": True,
            "uploaded_probe": True,
            "liveness": True,
        },
        "notes": [
            "Verification calls Azure Face API verify_face_to_person.",
            "Live camera verification is enforced via session-based liveness before similarity compare.",
        ],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
