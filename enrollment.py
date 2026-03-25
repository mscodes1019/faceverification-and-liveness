# import os
# import time
# from io import BytesIO
# from azure.cognitiveservices.vision.face import FaceClient
# from msrest.authentication import CognitiveServicesCredentials
# from azure.storage.blob import BlobServiceClient
# from dotenv import load_dotenv

# # ── Load environment variables ────────────────────────────────────────────────
# load_dotenv()

# FACE_KEY                     = os.getenv("FACE_API_KEY")
# FACE_ENDPOINT                = os.getenv("FACE_ENDPOINT_URL")
# AZURE_STORAGE_CONNECTION_STR = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
# CONTAINER_NAME               = os.getenv("BLOB_CONTAINER_NAME", "faceenrollimages")
# PERSON_GROUP_ID              = os.getenv("PERSON_GROUP_ID", "faceauth_person_group")

# # ── Validate credentials ──────────────────────────────────────────────────────
# if not FACE_KEY or not FACE_ENDPOINT:
#     raise EnvironmentError("FACE_API_KEY and FACE_ENDPOINT_URL must be set in your .env file.")
# if not AZURE_STORAGE_CONNECTION_STR:
#     raise EnvironmentError("AZURE_STORAGE_CONNECTION_STRING must be set in your .env file.")

# # ── Initialize Azure clients ──────────────────────────────────────────────────
# face_client         = FaceClient(FACE_ENDPOINT, CognitiveServicesCredentials(FACE_KEY))
# blob_service_client = BlobServiceClient.from_connection_string(AZURE_STORAGE_CONNECTION_STR)


# # ── Step 1: Setup Person Group ────────────────────────────────────────────────
# def setup_person_group():
#     """Check if Person Group exists. If not, create it."""
#     try:
#         face_client.person_group.get(PERSON_GROUP_ID)
#         print(f"✓ Person Group '{PERSON_GROUP_ID}' already exists.")
#     except Exception as e:
#         if "PersonGroupNotFound" in str(e):
#             print(f"Creating Person Group '{PERSON_GROUP_ID}'...")
#             face_client.person_group.create(
#                 person_group_id=PERSON_GROUP_ID,
#                 name=PERSON_GROUP_ID,
#                 recognition_model="recognition_04"
#             )
#             print(f"✓ Person Group created.")
#         else:
#             raise e


# # ── Step 2: Get all user folders from Blob Storage ───────────────────────────
# def get_user_folders_from_blob():
#     """
#     Scan Blob Storage container and return a list of unique user IDs.
#     Looks for blobs in the pattern: USR-XXXX/enroll.jpg
#     """
#     container_client = blob_service_client.get_container_client(CONTAINER_NAME)
#     blobs            = container_client.list_blobs()

#     user_ids = set()
#     for blob in blobs:
#         parts = blob.name.split("/")
#         if len(parts) == 2 and parts[1] == "enroll.jpg":
#             user_ids.add(parts[0])

#     return sorted(list(user_ids))


# # ── Step 3: Download image from Blob Storage ─────────────────────────────────
# def download_from_blob(blob_name: str) -> BytesIO:
#     """Download image from Blob Storage as a byte stream."""
#     container_client = blob_service_client.get_container_client(CONTAINER_NAME)
#     blob_client      = container_client.get_blob_client(blob_name)
#     blob_data        = blob_client.download_blob().readall()
#     return BytesIO(blob_data)


# # ── Step 4: Delete image from Blob Storage ───────────────────────────────────
# def delete_from_blob(blob_name: str):
#     """
#     Delete image from Blob Storage after template is generated.
#     AC1 — raw image must be discarded after enrollment.
#     """
#     container_client = blob_service_client.get_container_client(CONTAINER_NAME)
#     container_client.delete_blob(blob_name)
#     print(f"  ✓ Raw image deleted from Blob Storage.")


# # ── Step 5: Train Person Group ───────────────────────────────────────────────
# def train_person_group():
#     """Train the Person Group. Must be done after adding new faces."""
#     print("\nTraining Person Group...")
#     face_client.person_group.train(PERSON_GROUP_ID)
#     while True:
#         status = face_client.person_group.get_training_status(PERSON_GROUP_ID)
#         if status.status == "succeeded":
#             print("✓ Person Group trained successfully.")
#             break
#         elif status.status == "failed":
#             raise Exception("Person Group training failed.")
#         else:
#             print("  Training in progress...")
#             time.sleep(1)


# # ── Enroll a single user ──────────────────────────────────────────────────────
# def enroll_user(user_id: str, consent: bool, retention: bool = False):
#     """
#     Enroll a single user by reading their image from Blob Storage.
#     AC1 — consent required, raw image discarded after template generation
#           unless retention is explicitly enabled.
#     """
#     if not consent:
#         print(f"  ✗ Consent not given for {user_id}. Skipping.")
#         return {"user_id": user_id, "status": "skipped", "reason": "No consent"}

#     blob_name = f"{user_id}/enroll.jpg"

#     try:
#         # Download image from Blob Storage
#         print(f"  Downloading image from Blob Storage...")
#         image_stream = download_from_blob(blob_name)
#         print(f"  ✓ Image downloaded.")

#         # Create person in Person Group
#         person = face_client.person_group_person.create(
#             person_group_id=PERSON_GROUP_ID,
#             name=user_id,
#             user_data=user_id
#         )
#         print(f"  ✓ Person created. Person ID: {person.person_id}")

#         # Add face to Person
#         face_client.person_group_person.add_face_from_stream(
#             person_group_id=PERSON_GROUP_ID,
#             person_id=person.person_id,
#             image=image_stream
#         )
#         print(f"  ✓ Face template generated and stored in Person Group.")

#         # AC1 — delete raw image unless retention is enabled
#         if not retention:
#             delete_from_blob(blob_name)
#         else:
#             print(f"  ✓ Image retained in Blob Storage (retention enabled).")

#         return {
#             "user_id":   user_id,
#             "person_id": str(person.person_id),
#             "status":    "enrolled"
#         }

#     except Exception as e:
#         print(f"  ✗ Enrollment failed for {user_id}: {e}")
#         return {"user_id": user_id, "status": "failed", "error": str(e)}


# # ── Enroll all users from Blob Storage ───────────────────────────────────────
# def enroll_all_users(consent: bool = True, retention: bool = False):
#     """
#     Read all user folders from Blob Storage and enroll each person.

#     Expected Blob Storage structure:
#         faceenrollimages/
#             USR-0001/
#                 enroll.jpg
#             USR-0002/
#                 enroll.jpg
#     """
#     print(f"\n{'='*50}")
#     print("FACE ENROLLMENT SYSTEM")
#     print(f"{'='*50}")
#     print(f"Container    : {CONTAINER_NAME}")
#     print(f"Person Group : {PERSON_GROUP_ID}")
#     print(f"{'='*50}\n")

#     # Setup Person Group
#     setup_person_group()

#     # Get all user folders from Blob Storage
#     print("\nScanning Blob Storage for users...")
#     user_ids = get_user_folders_from_blob()

#     if not user_ids:
#         print("No user folders found in Blob Storage.")
#         print("Make sure your images are uploaded as: USR-XXXX/enroll.jpg")
#         return

#     print(f"Found {len(user_ids)} user(s) to enroll: {', '.join(user_ids)}\n")

#     # Enroll each user
#     results = []
#     for user_id in user_ids:
#         print(f"{'='*50}")
#         print(f"Enrolling: {user_id}")
#         result = enroll_user(user_id, consent, retention)
#         results.append(result)

#     # Train Person Group once after all enrollments
#     train_person_group()

#     # Print summary
#     print(f"\n{'='*50}")
#     print("ENROLLMENT SUMMARY")
#     print(f"{'='*50}")
#     enrolled = [r for r in results if r["status"] == "enrolled"]
#     failed   = [r for r in results if r["status"] == "failed"]
#     skipped  = [r for r in results if r["status"] == "skipped"]

#     print(f"✓ Enrolled : {len(enrolled)}")
#     print(f"✗ Failed   : {len(failed)}")
#     print(f"  Skipped  : {len(skipped)}")
#     print(f"{'='*50}")

#     for r in results:
#         icon = "✓" if r["status"] == "enrolled" else "✗"
#         print(f"  {icon} {r['user_id']} — {r['status']}")

#     print(f"{'='*50}\n")
#     return results


# # ── Run ───────────────────────────────────────────────────────────────────────
# if __name__ == "__main__":
#     enroll_all_users(
#         consent   = True,   # AC1 — must be True
#         retention = False   # AC1 — raw images discarded after enrollment
#     )










import os
import time
from io import BytesIO
from azure.cognitiveservices.vision.face import FaceClient
from msrest.authentication import CognitiveServicesCredentials
from azure.storage.blob import BlobServiceClient
from dotenv import load_dotenv

# ── Load environment variables ────────────────────────────────────────────────
load_dotenv()

FACE_KEY                     = os.getenv("FACE_API_KEY")
FACE_ENDPOINT                = os.getenv("FACE_ENDPOINT_URL")
AZURE_STORAGE_CONNECTION_STR = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
CONTAINER_NAME               = os.getenv("BLOB_CONTAINER_NAME", "msfacecontainer")
PERSON_GROUP_ID              = os.getenv("PERSON_GROUP_ID", "faceauth_person_group")
BLOB_PREFIX                  = os.getenv("BLOB_PREFIX", "face-enrollment-images")

# ── Validate credentials ──────────────────────────────────────────────────────
if not FACE_KEY or not FACE_ENDPOINT:
    raise EnvironmentError("FACE_API_KEY and FACE_ENDPOINT_URL must be set in your .env file.")
if not AZURE_STORAGE_CONNECTION_STR:
    raise EnvironmentError("AZURE_STORAGE_CONNECTION_STRING must be set in your .env file.")

# ── Initialize Azure clients ──────────────────────────────────────────────────
face_client         = FaceClient(FACE_ENDPOINT, CognitiveServicesCredentials(FACE_KEY))
blob_service_client = BlobServiceClient.from_connection_string(AZURE_STORAGE_CONNECTION_STR)


# ── Step 1: Setup Person Group ────────────────────────────────────────────────
def setup_person_group():
    """Check if Person Group exists. If not, create it."""
    try:
        face_client.person_group.get(PERSON_GROUP_ID)
        print(f"✓ Person Group '{PERSON_GROUP_ID}' already exists.")
    except Exception as e:
        if "PersonGroupNotFound" in str(e):
            print(f"Creating Person Group '{PERSON_GROUP_ID}'...")
            face_client.person_group.create(
                person_group_id=PERSON_GROUP_ID,
                name=PERSON_GROUP_ID,
                recognition_model="recognition_04"
            )
            print(f"✓ Person Group created.")
        else:
            raise e


# ── Step 2: Get all user folders from Blob Storage ───────────────────────────
def get_user_folders_from_blob():
    """
    Scan Blob Storage container and return a list of unique user IDs.
    Handles path: face-enrollment-images/USR-0001/enroll.jpg
    """
    container_client = blob_service_client.get_container_client(CONTAINER_NAME)
    blobs            = container_client.list_blobs(name_starts_with=f"{BLOB_PREFIX}/")

    user_ids = set()
    for blob in blobs:
        # Remove prefix: "face-enrollment-images/USR-0001/enroll.jpg"
        # becomes: "USR-0001/enroll.jpg"
        name = blob.name[len(f"{BLOB_PREFIX}/"):]
        parts = name.split("/")
        if len(parts) == 2 and parts[1] == "enroll.jpg":
            user_ids.add(parts[0])

    return sorted(list(user_ids))


# ── Step 3: Download image from Blob Storage ─────────────────────────────────
def download_from_blob(user_id: str) -> tuple:
    """Download image from Blob Storage as a byte stream."""
    blob_name        = f"{BLOB_PREFIX}/{user_id}/enroll.jpg"
    container_client = blob_service_client.get_container_client(CONTAINER_NAME)
    blob_client      = container_client.get_blob_client(blob_name)
    blob_data        = blob_client.download_blob().readall()
    return BytesIO(blob_data), blob_name


# ── Step 4: Delete image from Blob Storage ───────────────────────────────────
def delete_from_blob(blob_name: str):
    """
    Delete image from Blob Storage after template is generated.
    AC1 — raw image must be discarded after enrollment.
    """
    container_client = blob_service_client.get_container_client(CONTAINER_NAME)
    container_client.delete_blob(blob_name)
    print(f"  ✓ Raw image deleted from Blob Storage.")


# ── Step 5: Train Person Group ───────────────────────────────────────────────
def train_person_group():
    """Train the Person Group. Must be done after adding new faces."""
    print("\nTraining Person Group...")
    face_client.person_group.train(PERSON_GROUP_ID)
    while True:
        status = face_client.person_group.get_training_status(PERSON_GROUP_ID)
        if status.status == "succeeded":
            print("✓ Person Group trained successfully.")
            break
        elif status.status == "failed":
            raise Exception("Person Group training failed.")
        else:
            print("  Training in progress...")
            time.sleep(1)


# ── Enroll a single user ──────────────────────────────────────────────────────
def enroll_user(user_id: str, consent: bool, retention: bool = False):
    """
    Enroll a single user by reading their image from Blob Storage.
    AC1 — consent required, raw image discarded after template generation
          unless retention is explicitly enabled.
    """
    if not consent:
        print(f"  ✗ Consent not given for {user_id}. Skipping.")
        return {"user_id": user_id, "status": "skipped", "reason": "No consent"}

    try:
        # Download image from Blob Storage
        print(f"  Downloading image from Blob Storage...")
        image_stream, blob_name = download_from_blob(user_id)
        print(f"  ✓ Image downloaded from: {blob_name}")

        # Create person in Person Group
        person = face_client.person_group_person.create(
            person_group_id=PERSON_GROUP_ID,
            name=user_id,
            user_data=user_id
        )
        print(f"  ✓ Person created. Person ID: {person.person_id}")

        # Add face to Person
        face_client.person_group_person.add_face_from_stream(
            person_group_id=PERSON_GROUP_ID,
            person_id=person.person_id,
            image=image_stream
        )
        print(f"  ✓ Face template generated and stored in Person Group.")

        # AC1 — delete raw image unless retention is enabled
        if not retention:
            delete_from_blob(blob_name)
        else:
            print(f"  ✓ Image retained in Blob Storage (retention enabled).")

        return {
            "user_id":   user_id,
            "person_id": str(person.person_id),
            "status":    "enrolled"
        }

    except Exception as e:
        print(f"  ✗ Enrollment failed for {user_id}: {e}")
        return {"user_id": user_id, "status": "failed", "error": str(e)}


# ── Enroll all users from Blob Storage ───────────────────────────────────────
def enroll_all_users(consent: bool = True, retention: bool = False):
    """
    Read all user folders from Blob Storage and enroll each person.

    Blob Storage structure:
        msfacecontainer/
            face-enrollment-images/
                USR-0001/
                    enroll.jpg
                USR-0002/
                    enroll.jpg
    """
    print(f"\n{'='*50}")
    print("FACE ENROLLMENT SYSTEM")
    print(f"{'='*50}")
    print(f"Container    : {CONTAINER_NAME}")
    print(f"Prefix       : {BLOB_PREFIX}")
    print(f"Person Group : {PERSON_GROUP_ID}")
    print(f"{'='*50}\n")

    # Setup Person Group
    setup_person_group()

    # Get all user folders from Blob Storage
    print("\nScanning Blob Storage for users...")
    user_ids = get_user_folders_from_blob()

    if not user_ids:
        print("No user folders found in Blob Storage.")
        print(f"Make sure your images are at: {BLOB_PREFIX}/USR-XXXX/enroll.jpg")
        return

    print(f"Found {len(user_ids)} user(s) to enroll: {', '.join(user_ids)}\n")

    # Enroll each user
    results = []
    for user_id in user_ids:
        print(f"{'='*50}")
        print(f"Enrolling: {user_id}")
        result = enroll_user(user_id, consent, retention)
        results.append(result)

    # Train Person Group once after all enrollments
    train_person_group()

    # Print summary
    print(f"\n{'='*50}")
    print("ENROLLMENT SUMMARY")
    print(f"{'='*50}")
    enrolled = [r for r in results if r["status"] == "enrolled"]
    failed   = [r for r in results if r["status"] == "failed"]
    skipped  = [r for r in results if r["status"] == "skipped"]

    print(f"✓ Enrolled : {len(enrolled)}")
    print(f"✗ Failed   : {len(failed)}")
    print(f"  Skipped  : {len(skipped)}")
    print(f"{'='*50}")

    for r in results:
        icon = "✓" if r["status"] == "enrolled" else "✗"
        print(f"  {icon} {r['user_id']} — {r['status']}")

    print(f"{'='*50}\n")
    return results


# ── Run ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    enroll_all_users(
        consent   = True,   # AC1 — must be True
        retention = False   # AC1 — raw images discarded after enrollment
    )