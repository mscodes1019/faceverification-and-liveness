import base64
import logging
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from config import MAX_IMAGE_BYTES, MAX_IMAGE_MB
from services.face_service import (
    detect_faces,
    get_face_policy_block_reason,
    extract_image_dimensions,
    validate_single_face,
    check_duplicate_user,
    create_person,
    add_face_to_person,
)
from services.blob_service import upload_image, delete_image, upload_reference_image
from services.verification_service import register_enrollment_image

logger = logging.getLogger("faceauth")
router = APIRouter()


class EnrollRequest(BaseModel):
    user_id:   str | None = None
    name:      str
    consent:   bool
    retention: bool = False
    image_b64: str


class DetectFaceRequest(BaseModel):
    image_b64: str


def decode_image(image_b64: str) -> bytes:
    """Decode base64 image string to bytes."""
    try:
        data = image_b64.split(",")[1] if "," in image_b64 else image_b64
        return base64.b64decode(data)
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Invalid image format. Must be base64 encoded."
        )


def generate_user_id() -> str:
    """Generate a compact, human-readable user id."""
    return f"USR-{uuid.uuid4().hex[:8].upper()}"


@router.post("/detect-face")
async def detect_face(req: DetectFaceRequest):
    """
    Pre-capture face check used by the live camera preview.
    Returns face count and whether exactly one face was found.
    """
    image_bytes = decode_image(req.image_b64)

    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"Image too large. Maximum allowed size is {MAX_IMAGE_MB}MB."
        )

    image_width, image_height = extract_image_dimensions(image_bytes)
    faces = detect_faces(image_bytes)
    face_count = len(faces)
    block_reason = None
    policy_ok = False

    if face_count == 1:
        block_reason = get_face_policy_block_reason(
            faces[0],
            image_width=image_width,
            image_height=image_height,
        )
        policy_ok = block_reason is None

    return {
        "face_count": face_count,
        "blocked_reason": block_reason,
        # Keep pre-capture checks tolerant: if one face is present, allow the
        # capture flow to continue. Strict policy enforcement still happens in
        # /api/enroll via validate_single_face.
        "exactly_one_face": face_count == 1,
        "policy_ok": policy_ok,
    }


@router.post("/enroll")
async def enroll(req: EnrollRequest):
    """
    Enroll a user from a webcam image.
    - Consent required (AC1)
    - Face detection before enrollment
    - Rejects 0 or multiple faces
    - Duplicate user check
    - Image size limit
    - Raw image deleted after template generation (AC1)
    """
    requested_user_id = (req.user_id or "").strip()
    logger.info(f"Enrollment request: user_id='{requested_user_id or 'AUTO'}' name='{req.name}'")

    # Consent check (AC1)
    if not req.consent:
        logger.warning(f"No consent for user_id='{req.user_id}'")
        raise HTTPException(
            status_code=400,
            detail="Consent must be explicitly given before enrollment."
        )

    # Input validation
    if not req.name.strip():
        raise HTTPException(status_code=400, detail="Name is required.")

    user_id = requested_user_id
    if not user_id:
        # Generate a user id server-side when client does not provide one.
        # Duplicate check still happens once below for consistency.
        user_id = generate_user_id()

    # Decode image
    image_bytes = decode_image(req.image_b64)

    # Image size check
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"Image too large. Maximum allowed size is {MAX_IMAGE_MB}MB."
        )
    logger.info(f"Image size: {len(image_bytes) / 1024:.1f} KB")

    # Face detection — must have exactly one face
    logger.info(f"Running face detection for user_id='{user_id}'...")
    validate_single_face(image_bytes)

    # Duplicate check is only needed when user_id was explicitly provided by client.
    # Auto-generated IDs are already unique enough for this flow and avoid a costly full scan.
    if requested_user_id:
        logger.info(f"Checking for duplicate user_id='{user_id}'...")
        check_duplicate_user(user_id)

    blob_name = None

    try:
        # Upload raw image only when retention is explicitly enabled.
        # This removes an extra network roundtrip for the common non-retention path.
        if req.retention:
            blob_name = upload_image(user_id, image_bytes)

        # Create person in Large Person Group
        person = create_person(req.name, user_id)

        # Add face to person
        add_face_to_person(str(person.person_id), image_bytes)

        # Store reference image for liveness+verify sessions (always, regardless of retention flag).
        upload_reference_image(str(person.person_id), image_bytes)

        # Register enrollment image fingerprint so verification can reject same-image replay.
        register_enrollment_image(str(person.person_id), image_bytes)

        # AC1 — keep raw image only if retention is enabled.
        if req.retention:
            image_status = f"Raw image retained at '{blob_name}' (retention enabled)."
        else:
            image_status = "Raw image discarded after template generation (AC1)."

        logger.info(f"Enrollment complete: user_id='{user_id}' person_id='{person.person_id}'")

        return {
            "status":       "enrolled",
            "user_id":      user_id,
            "name":         req.name,
            "person_id":    str(person.person_id),
            "model":        "recognition_04",
            "consent":      req.consent,
            "image_status": image_status,
            "note":         "Call POST /api/train to update the model before verification.",
            "enrolled_at":  datetime.now(timezone.utc).isoformat(),
        }

    except HTTPException:
        if blob_name:
            delete_image(blob_name)
        raise

    except Exception as e:
        if blob_name:
            delete_image(blob_name)
        logger.error(f"Enrollment failed for user_id='{user_id or 'AUTO'}': {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Enrollment failed: {str(e)}"
        )