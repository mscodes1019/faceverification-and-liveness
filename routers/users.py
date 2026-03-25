import logging
from fastapi import APIRouter, HTTPException
from services.face_service import list_persons, delete_person

logger = logging.getLogger("faceauth")
router = APIRouter()


@router.get("/users")
async def get_users():
    """List all enrolled persons in the Large Person Group."""
    try:
        users = list_persons()
        logger.info(f"Listed {len(users)} enrolled users.")
        return {"total": len(users), "users": users}
    except Exception as e:
        logger.error(f"List users error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/users/{person_id}")
async def remove_user(person_id: str):
    """Delete an enrolled person from the Large Person Group."""
    try:
        delete_person(person_id)
        return {"status": "deleted", "person_id": person_id}
    except Exception as e:
        logger.error(f"Delete error for person_id='{person_id}': {e}")
        raise HTTPException(status_code=500, detail=str(e))