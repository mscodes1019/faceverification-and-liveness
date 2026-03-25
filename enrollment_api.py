import os
import time
import base64
import logging
import uuid
from io import BytesIO
from datetime import datetime, timezone

from azure.cognitiveservices.vision.face import FaceClient
from msrest.authentication import CognitiveServicesCredentials
from azure.storage.blob import BlobServiceClient
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("faceauth")

# ── Config from .env ───────────────────────────────────────────────────────────
FACE_KEY                   = os.getenv("FACE_API_KEY")
FACE_ENDPOINT              = os.getenv("FACE_ENDPOINT_URL")
AZURE_STORAGE_CONNECTION   = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
CONTAINER_NAME             = os.getenv("BLOB_CONTAINER_NAME", "msfacecontainer")
PERSON_GROUP_ID            = os.getenv("PERSON_GROUP_ID", "faceauth_large_group")
BLOB_PREFIX                = os.getenv("BLOB_PREFIX", "face-enrollment-images")

# Safety limits
MAX_IMAGE_SIZE_MB          = 4
MAX_IMAGE_BYTES            = MAX_IMAGE_SIZE_MB * 1024 * 1024

# ── Azure clients ──────────────────────────────────────────────────────────────
face_client         = FaceClient(FACE_ENDPOINT, CognitiveServicesCredentials(FACE_KEY))
blob_service_client = BlobServiceClient.from_connection_string(AZURE_STORAGE_CONNECTION)

# ── FastAPI app ────────────────────────────────────────────────────────────────
app = FastAPI(title="FaceAuth Enrollment API", version="2.0.0")

# CORS — restrict to your domain in production
# Replace "*" with your actual frontend URL e.g. "https://yourdomain.com"
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8000", "http://127.0.0.1:8000"],
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type"],
)

# Serve frontend
frontend_dir = os.path.join(os.path.dirname(__file__), "frontend")
if os.path.isdir(frontend_dir):
    app.mount("/static", StaticFiles(directory=frontend_dir), name="static")

@app.get("/")
async def root():
    index = os.path.join(frontend_dir, "enroll.html")
    if os.path.exists(index):
        return FileResponse(index)
    return {"message": "FaceAuth Enrollment API v2. Visit /docs"}


# ── Request / Response models ──────────────────────────────────────────────────
class EnrollRequest(BaseModel):
    user_id:   str
    name:      str
    consent:   bool
    retention: bool = False
    image_b64: str


class TrainRequest(BaseModel):
    wait_for_completion: bool = True


# ── Startup: ensure resources exist ───────────────────────────────────────────
@app.on_event("startup")
async def startup():
    logger.info("FaceAuth API starting up...")
    setup_blob_container()
    setup_large_person_group()
    logger.info("Startup complete. All Azure resources ready.")


def setup_blob_container():
    """Auto-create blob container if it does not exist."""
    try:
        container_client = blob_service_client.get_container_client(CONTAINER_NAME)
        container_client.get_container_properties()
        logger.info(f"Blob container '{CONTAINER_NAME}' exists.")
    except Exception:
        logger.info(f"Blob container '{CONTAINER_NAME}' not found. Creating...")
        blob_service_client.create_container(CONTAINER_NAME)
        logger.info(f"Blob container '{CONTAINER_NAME}' created.")


def setup_large_person_group():
    """Auto-create Large Person Group if it does not exist."""
    try:
        face_client.large_person_group.get(PERSON_GROUP_ID)
        logger.info(f"Large Person Group '{PERSON_GROUP_ID}' exists.")
    except Exception as e:
        if "LargePersonGroupNotFound" in str(e) or "NotFound" in str(e):
            logger.info(f"Large Person Group '{PERSON_GROUP_ID}' not found. Creating...")
            face_client.large_person_group.create(
                large_person_group_id=PERSON_GROUP_ID,
                name="FaceAuth Building Access",
                recognition_model="recognition_04",
            )
            logger.info(f"Large Person Group '{PERSON_GROUP_ID}' created.")
        else:
            logger.error(f"Error checking Large Person Group: {e}")
            raise


# ── Helpers ────────────────────────────────────────────────────────────────────

def decode_image(image_b64: str) -> bytes:
    """Decode base64 image string to bytes."""
    try:
        data = image_b64.split(",")[1] if "," in image_b64 else image_b64
        return base64.b64decode(data)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid image format. Must be base64 encoded.")


def validate_image_size(image_bytes: bytes):
    """Reject images that are too large."""
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"Image too large. Maximum size is {MAX_IMAGE_SIZE_MB}MB."
        )


def detect_faces(image_bytes: bytes) -> list:
    """
    Detect faces in image using Azure Face API.
    Returns list of detected faces.
    """
    try:
        detected = face_client.face.detect_with_stream(
            image=BytesIO(image_bytes),
            detection_model="detection_03",
            recognition_model="recognition_04",
            return_face_id=False,
        )
        return detected
    except Exception as e:
        logger.error(f"Face detection error: {e}")
        raise HTTPException(status_code=500, detail=f"Face detection failed: {str(e)}")


def validate_single_face(image_bytes: bytes):
    """
    Ensures exactly one face is in the image.
    Raises HTTPException if 0 or more than 1 face detected.
    """
    faces = detect_faces(image_bytes)

    if len(faces) == 0:
        raise HTTPException(
            status_code=400,
            detail="No face detected in the image. Please ensure your face is clearly visible and try again."
        )

    if len(faces) > 1:
        raise HTTPException(
            status_code=400,
            detail=f"{len(faces)} faces detected. Please ensure only one person is in the frame."
        )

    return faces[0]


def check_duplicate_user(user_id: str):
    """
    Check if user_id already enrolled in Large Person Group.
    Raises HTTPException if duplicate found.
    """
    try:
        persons = face_client.large_person_group_person.list(PERSON_GROUP_ID)
        for person in persons:
            if person.user_data == user_id:
                raise HTTPException(
                    status_code=409,
                    detail=f"User '{user_id}' is already enrolled. Use DELETE /api/users/{{person_id}} to remove first, then re-enroll."
                )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Duplicate check error: {e}")
        raise HTTPException(status_code=500, detail=f"Could not verify duplicate status: {str(e)}")


def upload_to_blob(user_id: str, image_bytes: bytes) -> str:
    """Upload image to blob with unique filename. Returns blob name."""
    unique_id  = str(uuid.uuid4())[:8]
    timestamp  = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    blob_name  = f"{BLOB_PREFIX}/{user_id}/{timestamp}_{unique_id}.jpg"
    container_client = blob_service_client.get_container_client(CONTAINER_NAME)
    container_client.upload_blob(name=blob_name, data=image_bytes, overwrite=True)
    logger.info(f"Uploaded blob: {blob_name}")
    return blob_name


def delete_from_blob(blob_name: str):
    """Delete a blob by name."""
    try:
        blob_service_client.get_container_client(CONTAINER_NAME).delete_blob(blob_name)
        logger.info(f"Deleted blob: {blob_name}")
    except Exception as e:
        logger.warning(f"Could not delete blob '{blob_name}': {e}")


# ── ENDPOINTS ──────────────────────────────────────────────────────────────────

# ── POST /api/enroll ───────────────────────────────────────────────────────────
@app.post("/api/enroll")
async def enroll(req: EnrollRequest):
    """
    Enroll a user from a webcam image.

    AC1 — consent required, raw image discarded after template generation.
    Improvements:
      - Face detection before enrollment
      - Rejects multiple faces
      - Duplicate user check
      - Image size limit
      - Unique blob filenames
      - Logging
    """

    logger.info(f"Enrollment request received for user_id='{req.user_id}' name='{req.name}'")

    # ── Validate consent (AC1) ─────────────────────────────────────────────
    if not req.consent:
        logger.warning(f"Enrollment rejected — no consent for user_id='{req.user_id}'")
        raise HTTPException(status_code=400, detail="Consent must be explicitly given before enrollment.")

    # ── Validate inputs ────────────────────────────────────────────────────
    if not req.user_id.strip():
        raise HTTPException(status_code=400, detail="User ID is required.")
    if not req.name.strip():
        raise HTTPException(status_code=400, detail="Name is required.")

    # ── Decode image ───────────────────────────────────────────────────────
    image_bytes = decode_image(req.image_b64)

    # ── Check image size ───────────────────────────────────────────────────
    validate_image_size(image_bytes)
    logger.info(f"Image size: {len(image_bytes) / 1024:.1f} KB")

    # ── Face detection — must have exactly one face ────────────────────────
    logger.info(f"Running face detection for user_id='{req.user_id}'...")
    validate_single_face(image_bytes)
    logger.info(f"Face detected successfully for user_id='{req.user_id}'")

    # ── Duplicate check ────────────────────────────────────────────────────
    logger.info(f"Checking for duplicate user_id='{req.user_id}'...")
    check_duplicate_user(req.user_id)

    blob_name = None

    try:
        # ── Upload to blob (temporary) ─────────────────────────────────────
        blob_name = upload_to_blob(req.user_id, image_bytes)

        # ── Create person in Large Person Group ────────────────────────────
        person = face_client.large_person_group_person.create(
            large_person_group_id=PERSON_GROUP_ID,
            name=req.name,
            user_data=req.user_id,
        )
        logger.info(f"Created person: person_id='{person.person_id}' for user_id='{req.user_id}'")

        # ── Add face to person ─────────────────────────────────────────────
        face_client.large_person_group_person.add_face_from_stream(
            large_person_group_id=PERSON_GROUP_ID,
            person_id=person.person_id,
            image=BytesIO(image_bytes),
            detection_model="detection_03",
        )
        logger.info(f"Face added to person_id='{person.person_id}'")

        # ── AC1: Delete raw image unless retention enabled ─────────────────
        if not req.retention:
            delete_from_blob(blob_name)
            image_status = "Raw image discarded after template generation (AC1 compliant)."
        else:
            image_status = f"Raw image retained in Blob Storage at '{blob_name}' (retention enabled)."

        logger.info(f"Enrollment complete for user_id='{req.user_id}' person_id='{person.person_id}'")

        return {
            "status":       "enrolled",
            "user_id":      req.user_id,
            "name":         req.name,
            "person_id":    str(person.person_id),
            "model":        "recognition_04",
            "consent":      req.consent,
            "image_status": image_status,
            "note":         "Call POST /api/train to update the model before verification.",
            "enrolled_at":  datetime.now(timezone.utc).isoformat(),
        }

    except HTTPException:
        # Clean up blob if something went wrong
        if blob_name:
            delete_from_blob(blob_name)
        raise

    except Exception as e:
        if blob_name:
            delete_from_blob(blob_name)
        logger.error(f"Enrollment failed for user_id='{req.user_id}': {e}")
        raise HTTPException(status_code=500, detail=f"Enrollment failed: {str(e)}")


# ── POST /api/train ────────────────────────────────────────────────────────────
@app.post("/api/train")
async def train(req: TrainRequest = TrainRequest()):
    """
    Train the Large Person Group.

    Call this after enrolling one or more users.
    Training is no longer done automatically on each enrollment
    to avoid slow responses and unnecessary API calls.
    """
    logger.info("Training Large Person Group...")

    try:
        face_client.large_person_group.train(PERSON_GROUP_ID)

        if req.wait_for_completion:
            for attempt in range(60):
                status = face_client.large_person_group.get_training_status(PERSON_GROUP_ID)
                if status.status == "succeeded":
                    logger.info("Large Person Group training succeeded.")
                    return {
                        "status":      "trained",
                        "message":     "Large Person Group trained successfully.",
                        "trained_at":  datetime.now(timezone.utc).isoformat(),
                    }
                elif status.status == "failed":
                    logger.error("Training failed.")
                    raise HTTPException(status_code=500, detail="Person Group training failed.")
                logger.info(f"Training in progress... attempt {attempt + 1}/60")
                time.sleep(2)
            raise HTTPException(status_code=504, detail="Training timed out after 120 seconds.")
        else:
            return {
                "status":  "training_started",
                "message": "Training started. Poll GET /api/train/status to check progress.",
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Training error: {e}")
        raise HTTPException(status_code=500, detail=f"Training failed: {str(e)}")


# ── GET /api/train/status ──────────────────────────────────────────────────────
@app.get("/api/train/status")
async def train_status():
    """Get the current training status of the Large Person Group."""
    try:
        status = face_client.large_person_group.get_training_status(PERSON_GROUP_ID)
        return {
            "status":  status.status,
            "message": status.message if hasattr(status, "message") else None,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── GET /api/users ─────────────────────────────────────────────────────────────
@app.get("/api/users")
async def list_users():
    """List all enrolled persons in the Large Person Group."""
    try:
        persons = face_client.large_person_group_person.list(PERSON_GROUP_ID)
        users = [
            {
                "person_id": str(p.person_id),
                "name":      p.name,
                "user_id":   p.user_data,
            }
            for p in persons
        ]
        logger.info(f"Listed {len(users)} enrolled users.")
        return {"total": len(users), "users": users}
    except Exception as e:
        logger.error(f"List users error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── DELETE /api/users/{person_id} ─────────────────────────────────────────────
@app.delete("/api/users/{person_id}")
async def delete_user(person_id: str):
    """Delete an enrolled person from the Large Person Group."""
    try:
        face_client.large_person_group_person.delete(PERSON_GROUP_ID, person_id)
        logger.info(f"Deleted person_id='{person_id}'")
        return {"status": "deleted", "person_id": person_id}
    except Exception as e:
        logger.error(f"Delete user error for person_id='{person_id}': {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── GET /api/health ────────────────────────────────────────────────────────────
@app.get("/api/health")
async def health():
    """Check connectivity to Face API and Blob Storage."""
    results = {}

    try:
        face_client.large_person_group.get(PERSON_GROUP_ID)
        results["face_api"]          = "connected"
        results["large_person_group"] = PERSON_GROUP_ID
    except Exception as e:
        results["face_api"] = f"error: {str(e)}"

    try:
        blob_service_client.get_container_client(CONTAINER_NAME).get_container_properties()
        results["blob_storage"] = "connected"
        results["container"]    = CONTAINER_NAME
    except Exception as e:
        results["blob_storage"] = f"error: {str(e)}"

    results["timestamp"] = datetime.now(timezone.utc).isoformat()
    return results