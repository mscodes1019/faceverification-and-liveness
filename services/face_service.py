import logging
import struct
from io import BytesIO
from azure.cognitiveservices.vision.face import FaceClient
from msrest.authentication import CognitiveServicesCredentials
from fastapi import HTTPException
from config import FACE_KEY, FACE_ENDPOINT, PERSON_GROUP_ID

logger = logging.getLogger("faceauth")

face_client = FaceClient(
    FACE_ENDPOINT,
    CognitiveServicesCredentials(FACE_KEY)
)


def setup_large_person_group():
    """Create Large Person Group if it does not exist."""
    try:
        face_client.large_person_group.get(PERSON_GROUP_ID)
        logger.info(f"Large Person Group '{PERSON_GROUP_ID}' exists.")
    except Exception as e:
        if "LargePersonGroupNotFound" in str(e) or "NotFound" in str(e):
            logger.info(f"Creating Large Person Group '{PERSON_GROUP_ID}'...")
            face_client.large_person_group.create(
                large_person_group_id=PERSON_GROUP_ID,
                name="FaceAuth Building Access",
                recognition_model="recognition_04",
            )
            logger.info(f"Large Person Group '{PERSON_GROUP_ID}' created.")
        else:
            logger.error(f"Error checking Large Person Group: {e}")
            raise


def detect_faces(image_bytes: bytes) -> list:
    """Detect all faces in image. Returns list of detected faces."""
    try:
        logger.info(f"[DEBUG] detect_faces: image_bytes={len(image_bytes)} bytes")
        detected = face_client.face.detect_with_stream(
            image=BytesIO(image_bytes),
            detection_model="detection_03",
            recognition_model="recognition_04",
            return_face_attributes=["mask", "glasses", "occlusion"],
            return_face_id=False,
        )
        logger.info(f"[DEBUG] detect_faces: Azure returned {len(detected)} faces: {detected}")
        return detected
    except Exception as e:
        logger.error(f"[DEBUG] Face detection error: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Face detection failed: {str(e)}"
        )


def is_face_masked(face) -> bool | None:
    """Return True if masked, False if clearly unmasked, None if unknown."""
    attrs = getattr(face, "face_attributes", None)
    if attrs is None:
        return None

    mask = getattr(attrs, "mask", None)
    if mask is None:
        return None

    mask_type = getattr(mask, "type", None)
    if mask_type is not None:
        mt = str(mask_type).lower()
        if "nomask" in mt or "no_mask" in mt:
            return False
        if "mask" in mt:
            return True

    nose_mouth = getattr(mask, "nose_and_mouth_covered", None)
    forehead = getattr(mask, "forehead_covered", None)
    if nose_mouth is None and forehead is None:
        return None

    return bool(nose_mouth) or bool(forehead)


def is_face_wearing_glasses(face) -> bool | None:
    """Return True if glasses are detected, False if none, None if unknown."""
    attrs = getattr(face, "face_attributes", None)
    if attrs is None:
        return None

    glasses = getattr(attrs, "glasses", None)
    if glasses is None:
        return None

    g = str(glasses).lower()
    if "noglasses" in g or "no_glasses" in g:
        return False
    return True


def is_face_occluded(face) -> bool | None:
    """Return True if important facial areas are occluded, False if clear, None if unknown."""
    attrs = getattr(face, "face_attributes", None)
    if attrs is None:
        return None

    occ = getattr(attrs, "occlusion", None)
    if occ is None:
        return None

    forehead = getattr(occ, "forehead_occluded", None)
    eyes = getattr(occ, "eye_occluded", None)
    mouth = getattr(occ, "mouth_occluded", None)

    if forehead is None and eyes is None and mouth is None:
        return None

    return bool(forehead) or bool(eyes) or bool(mouth)


def extract_image_dimensions(image_bytes: bytes) -> tuple[int | None, int | None]:
    """Return (width, height) for common JPEG/PNG formats; otherwise (None, None)."""
    if not image_bytes or len(image_bytes) < 24:
        return None, None

    try:
        # PNG: signature + IHDR chunk contains width/height at fixed offsets.
        if image_bytes.startswith(b"\x89PNG\r\n\x1a\n") and len(image_bytes) >= 24:
            width = struct.unpack(">I", image_bytes[16:20])[0]
            height = struct.unpack(">I", image_bytes[20:24])[0]
            if width > 0 and height > 0:
                return width, height

        # JPEG: scan for SOF marker containing width/height.
        if image_bytes[0:2] == b"\xff\xd8":
            i = 2
            while i + 9 < len(image_bytes):
                if image_bytes[i] != 0xFF:
                    i += 1
                    continue

                marker = image_bytes[i + 1]
                i += 2

                # Markers without payload.
                if marker in (0xD8, 0xD9):
                    continue

                if i + 2 > len(image_bytes):
                    break
                segment_length = struct.unpack(">H", image_bytes[i:i + 2])[0]
                if segment_length < 2 or i + segment_length > len(image_bytes):
                    break

                # SOF0..SOF3/5..7/9..11/13..15 contain frame dimensions.
                if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                    if i + 7 < len(image_bytes):
                        height = struct.unpack(">H", image_bytes[i + 3:i + 5])[0]
                        width = struct.unpack(">H", image_bytes[i + 5:i + 7])[0]
                        if width > 0 and height > 0:
                            return width, height

                i += segment_length
    except Exception:
        return None, None

    return None, None


def _get_face_rect(face):
    rect = getattr(face, "face_rectangle", None)
    if rect is None:
        return None
    left = getattr(rect, "left", None)
    top = getattr(rect, "top", None)
    width = getattr(rect, "width", None)
    height = getattr(rect, "height", None)
    if None in (left, top, width, height):
        return None
    try:
        return float(left), float(top), float(width), float(height)
    except Exception:
        return None


def _face_geometry_block_reason(face, image_width: int | None, image_height: int | None) -> str | None:
    if not image_width or not image_height:
        return None

    rect = _get_face_rect(face)
    if rect is None:
        return None

    left, top, width, height = rect
    if width <= 0 or height <= 0:
        return "Face could not be measured. Please face the camera directly and try again."

    image_area = float(image_width) * float(image_height)
    face_area_ratio = (width * height) / image_area

    # Require sufficient face coverage to avoid distant/accidental detections.
    if face_area_ratio < 0.08:
        return "Move closer so your face fills more of the frame."

    # Ensure face is near center to reduce off-angle/partial captures.
    face_cx = left + (width / 2.0)
    face_cy = top + (height / 2.0)
    norm_dx = abs(face_cx - (float(image_width) / 2.0)) / float(image_width)
    norm_dy = abs(face_cy - (float(image_height) / 2.0)) / float(image_height)
    if norm_dx > 0.22 or norm_dy > 0.25:
        return "Center your face in the frame and keep your head straight."

    return None


def get_face_policy_block_reason(face, image_width: int | None = None, image_height: int | None = None) -> str | None:
    """Return human-readable reason when face violates enrollment policy."""
    if is_face_masked(face) is True:
        return "Face mask detected. Please remove mask and try again."
    if is_face_wearing_glasses(face) is True:
        return "Glasses detected. Please remove glasses and try again."
    if is_face_occluded(face) is True:
        return "Face occlusion detected (cap/covered eyes/forehead). Please uncover your face and try again."

    geometry_reason = _face_geometry_block_reason(face, image_width, image_height)
    if geometry_reason:
        return geometry_reason

    return None


def validate_single_face(image_bytes: bytes):
    """
    Ensure exactly one face is in the image.
    Raises HTTPException if 0 or more than 1 face found.
    """
    faces = detect_faces(image_bytes)

    if len(faces) == 0:
        raise HTTPException(
            status_code=400,
            detail="No face detected. Please ensure your face is clearly visible and try again."
        )
    if len(faces) > 1:
        raise HTTPException(
            status_code=400,
            detail=f"{len(faces)} faces detected. Please ensure only one person is in the frame."
        )

    image_width, image_height = extract_image_dimensions(image_bytes)
    face = faces[0]
    reason = get_face_policy_block_reason(face, image_width=image_width, image_height=image_height)
    if reason:
        raise HTTPException(status_code=400, detail=reason)

    logger.info("Single face validated successfully.")
    return face


def check_duplicate_user(user_id: str):
    """
    Check if user_id already exists in Large Person Group.
    Raises 409 if duplicate found.
    """
    try:
        persons = face_client.large_person_group_person.list(PERSON_GROUP_ID)
        for person in persons:
            if person.user_data == user_id:
                raise HTTPException(
                    status_code=409,
                    detail=f"User '{user_id}' is already enrolled. Delete existing record first."
                )
        logger.info(f"No duplicate found for user_id='{user_id}'.")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Duplicate check error: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Could not verify duplicate status: {str(e)}"
        )


def create_person(name: str, user_id: str):
    """Create a new person in the Large Person Group."""
    person = face_client.large_person_group_person.create(
        large_person_group_id=PERSON_GROUP_ID,
        name=name,
        user_data=user_id,
    )
    logger.info(f"Created person person_id='{person.person_id}' for user_id='{user_id}'")
    return person


def add_face_to_person(person_id: str, image_bytes: bytes):
    """Add face to an existing person in the Large Person Group."""
    face_client.large_person_group_person.add_face_from_stream(
        large_person_group_id=PERSON_GROUP_ID,
        person_id=person_id,
        image=BytesIO(image_bytes),
        detection_model="detection_03",
    )
    logger.info(f"Face added to person_id='{person_id}'")


def delete_person(person_id: str):
    """Delete a person from the Large Person Group."""
    face_client.large_person_group_person.delete(
        PERSON_GROUP_ID,
        person_id
    )
    logger.info(f"Deleted person_id='{person_id}'")


def list_persons():
    """List all persons in the Large Person Group."""
    persons = face_client.large_person_group_person.list(PERSON_GROUP_ID)
    return [
        {
            "person_id": str(p.person_id),
            "name":      p.name,
            "user_id":   p.user_data,
        }
        for p in persons
    ]


def train_group():
    """Trigger training of the Large Person Group."""
    face_client.large_person_group.train(PERSON_GROUP_ID)
    logger.info("Training triggered.")


def get_training_status():
    """Get current training status."""
    return face_client.large_person_group.get_training_status(PERSON_GROUP_ID)


def health_check():
    """Ping Face API by fetching the group."""
    face_client.large_person_group.get(PERSON_GROUP_ID)