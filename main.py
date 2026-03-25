import logging
import os
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from config import ALLOWED_ORIGINS
from services.face_service import setup_large_person_group
from services.blob_service import setup_blob_container
from routers import enroll, users, train, health, verify, liveness, identify, edge

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("faceauth")

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("FaceAuth API starting up...")
    async def warmup_azure_resources():
        try:
            await asyncio.to_thread(setup_blob_container)
            await asyncio.to_thread(setup_large_person_group)
            app.state.azure_ready = True
            logger.info("All Azure resources ready.")
        except Exception as e:
            logger.error("Azure warm-up failed: %s", e)
    asyncio.create_task(warmup_azure_resources())
    logger.info("Startup complete. Azure resources are warming up in background.")
    yield


app = FastAPI(title="FaceAuth Enrollment API", version="2.0.0", lifespan=lifespan)
app.state.azure_ready = False

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type"],
)

# Paths
frontend_dir = os.path.join(os.path.dirname(__file__), "frontend")
css_dir      = os.path.join(frontend_dir, "css")
js_dir       = os.path.join(frontend_dir, "js")
assets_dir   = os.path.join(frontend_dir, "assets")
legacy_liveness_ui_dir = os.path.join(frontend_dir, "legacy-liveness")
legacy_liveness_assets_dir = os.path.join(legacy_liveness_ui_dir, "facelivenessdetector-assets")

# Mount css, js and assets so the browser can find them
app.mount("/css",    StaticFiles(directory=css_dir),    name="css")
app.mount("/js",     StaticFiles(directory=js_dir),     name="js")
app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")
if os.path.isdir(legacy_liveness_ui_dir):
    app.mount(
        "/legacy/azure-ai-vision-face-ui",
        StaticFiles(directory=legacy_liveness_ui_dir),
        name="legacy-azure-ai-vision-face-ui",
    )
    # Compatibility route required by legacy FaceLivenessDetector.js, which loads
    # ./facelivenessdetector-assets/* relative to site root.
    if os.path.isdir(legacy_liveness_assets_dir):
        app.mount(
            "/facelivenessdetector-assets",
            StaticFiles(directory=legacy_liveness_assets_dir),
            name="legacy-facelivenessdetector-assets",
        )
else:
    logger.warning("Legacy liveness UI folder not found: %s", legacy_liveness_ui_dir)


@app.get("/")
async def root():
    index = os.path.join(frontend_dir, "enroll.html")
    if os.path.exists(index):
        return FileResponse(index)
    return {"message": "FaceAuth API running. Visit /docs"}


@app.get("/enroll.html")
async def enroll_page():
    index = os.path.join(frontend_dir, "enroll.html")
    if os.path.exists(index):
        return FileResponse(index)
    return {"message": "FaceAuth API running. Visit /docs"}


@app.get("/liveness-check.html")
async def liveness_page():
    page = os.path.join(frontend_dir, "liveness-check.html")
    if os.path.exists(page):
        return FileResponse(page)
    return {"message": "Liveness page not found."}


@app.get("/liveness-test.html")
async def liveness_test_page():
    page = os.path.join(frontend_dir, "liveness-test.html")
    if os.path.exists(page):
        return FileResponse(page)
    return {"message": "Liveness test page not found."}


app.include_router(enroll.router, prefix="/api")
app.include_router(users.router,  prefix="/api")
app.include_router(train.router,  prefix="/api")
app.include_router(health.router, prefix="/api")
app.include_router(verify.router, prefix="/api")
app.include_router(liveness.router, prefix="/api")
app.include_router(identify.router, prefix="/api")
app.include_router(edge.router, prefix="/api")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
