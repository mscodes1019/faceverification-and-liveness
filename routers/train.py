import time
import logging
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from services.face_service import train_group, get_training_status

logger = logging.getLogger("faceauth")
router = APIRouter()


class TrainRequest(BaseModel):
    wait_for_completion: bool = True


@router.post("/train")
async def train(req: TrainRequest = TrainRequest()):
    """
    Train the Large Person Group.
    Call this after enrolling one or more users.
    """
    logger.info("Training Large Person Group...")

    try:
        train_group()

        if req.wait_for_completion:
            for attempt in range(60):
                status = get_training_status()
                if status.status == "succeeded":
                    logger.info("Training succeeded.")
                    return {
                        "status":     "trained",
                        "message":    "Large Person Group trained successfully.",
                        "trained_at": datetime.now(timezone.utc).isoformat(),
                    }
                elif status.status == "failed":
                    logger.error("Training failed.")
                    raise HTTPException(
                        status_code=500,
                        detail="Person Group training failed."
                    )
                logger.info(f"Training in progress... attempt {attempt + 1}/60")
                time.sleep(2)

            raise HTTPException(
                status_code=504,
                detail="Training timed out after 120 seconds."
            )
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


@router.get("/train/status")
async def training_status():
    """Get current training status of the Large Person Group."""
    try:
        status = get_training_status()
        return {
            "status":  status.status,
            "message": getattr(status, "message", None),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))