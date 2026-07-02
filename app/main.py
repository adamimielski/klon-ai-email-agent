"""FastAPI entry — serwuje frontend + API."""
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from .config import settings
from .db import init_db
from .api.mails import router as mails_router
from .api.proposals import router as proposals_router


app = FastAPI(title="Mail Dashboard")

STATIC_DIR = Path(__file__).parent / "static"


@app.on_event("startup")
def on_startup():
    init_db()


@app.get("/health")
def health():
    return {"status": "ok", "service": "mail-dashboard"}


app.include_router(mails_router)
app.include_router(proposals_router)

# Frontend
@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


# Wszelkie inne ścieżki statyczne
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
