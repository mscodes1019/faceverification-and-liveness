import uuid
import logging
from datetime import datetime, timezone
from azure.storage.blob import BlobServiceClient
from config import (
    AZURE_STORAGE_CONNECTION,
    AZURE_STORAGE_ACCOUNT_URL,
    AZURE_STORAGE_AUTH_MODE,
    CONTAINER_NAME,
    BLOB_PREFIX,
)

try:
    from azure.identity import DefaultAzureCredential
except Exception:  # pragma: no cover - optional dependency in connection-string mode
    DefaultAzureCredential = None

logger = logging.getLogger("faceauth")


def _create_blob_service_client() -> BlobServiceClient:
    """
    Create BlobServiceClient using configured auth mode.
    - managed_identity: RBAC using DefaultAzureCredential and account URL
    - connection_string: key-based fallback for local/dev environments
    """
    auth_mode = (AZURE_STORAGE_AUTH_MODE or "connection_string").strip().lower()

    if auth_mode != "managed_identity":
        raise RuntimeError(
            "AC2 enforcement: set AZURE_STORAGE_AUTH_MODE=managed_identity. "
            "Connection-string auth is disabled in this build."
        )

    if not AZURE_STORAGE_ACCOUNT_URL:
        raise RuntimeError(
            "AZURE_STORAGE_ACCOUNT_URL is required when AZURE_STORAGE_AUTH_MODE=managed_identity."
        )
    if DefaultAzureCredential is None:
        raise RuntimeError(
            "Managed identity mode requires azure-identity package. Install it first."
        )

    credential = DefaultAzureCredential(exclude_interactive_browser_credential=False)
    logger.info("Blob auth mode: managed_identity")
    return BlobServiceClient(account_url=AZURE_STORAGE_ACCOUNT_URL, credential=credential)


blob_service_client = _create_blob_service_client()


def setup_blob_container():
    """Auto-create blob container if it does not exist."""
    try:
        container_client = blob_service_client.get_container_client(CONTAINER_NAME)
        container_client.get_container_properties()
        logger.info(f"Blob container '{CONTAINER_NAME}' exists.")
    except Exception:
        logger.info(f"Creating blob container '{CONTAINER_NAME}'...")
        blob_service_client.create_container(CONTAINER_NAME)
        logger.info(f"Blob container '{CONTAINER_NAME}' created.")


def upload_image(user_id: str, image_bytes: bytes) -> str:
    """
    Upload image to blob with unique filename.
    Returns the blob name.
    """
    unique_id  = str(uuid.uuid4())[:8]
    timestamp  = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    blob_name  = f"{BLOB_PREFIX}/{user_id}/{timestamp}_{unique_id}.jpg"

    container_client = blob_service_client.get_container_client(CONTAINER_NAME)
    container_client.upload_blob(
        name=blob_name,
        data=image_bytes,
        overwrite=True
    )
    logger.info(f"Uploaded blob: {blob_name}")
    return blob_name


def delete_image(blob_name: str) -> bool:
    """Delete a blob by name. Returns True on success, False on failure."""
    try:
        blob_service_client \
            .get_container_client(CONTAINER_NAME) \
            .delete_blob(blob_name)
        logger.info(f"Deleted blob: {blob_name}")
        return True
    except Exception as e:
        logger.warning(f"Could not delete blob '{blob_name}': {e}")
        return False


def health_check():
    """Ping Blob Storage by fetching container properties."""
    blob_service_client \
        .get_container_client(CONTAINER_NAME) \
        .get_container_properties()


# ── Reference image storage (used for liveness+verify sessions) ───────────────

REFERENCE_BLOB_PREFIX = "face-references"


def upload_reference_image(person_id: str, image_bytes: bytes) -> None:
    """Store a reference face image keyed by person_id for liveness+verify sessions."""
    if not person_id or not image_bytes:
        return
    blob_name = f"{REFERENCE_BLOB_PREFIX}/{person_id}.jpg"
    container_client = blob_service_client.get_container_client(CONTAINER_NAME)
    container_client.upload_blob(name=blob_name, data=image_bytes, overwrite=True)
    logger.info(f"Stored reference image for person_id='{person_id}' at blob='{blob_name}'")


def download_reference_image(person_id: str) -> bytes:
    """Download stored reference face bytes for a person_id."""
    from fastapi import HTTPException
    if not person_id or not person_id.strip():
        raise HTTPException(status_code=400, detail="person_id is required.")
    blob_name = f"{REFERENCE_BLOB_PREFIX}/{person_id.strip()}.jpg"
    try:
        blob_client = blob_service_client.get_blob_client(container=CONTAINER_NAME, blob=blob_name)
        return blob_client.download_blob().readall()
    except Exception as e:
        err = str(e).lower()
        if "blobnotfound" in err or "not found" in err or "404" in err:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"Reference image not found for person '{person_id}'. "
                    "Ensure enrollment was completed after this feature was deployed."
                ),
            )
        raise HTTPException(status_code=500, detail=f"Could not retrieve reference image: {e}")