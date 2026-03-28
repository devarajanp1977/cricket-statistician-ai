"""Cricket Statistician AI - FastAPI Application"""
import os
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

from .cricket.router import router as cricket_router

app = FastAPI(
    title="Cricket Statistician AI",
    description="AI-powered cricket statistics analysis agent",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(cricket_router)


@app.get("/")
async def root():
    """Serve the cricket statistician UI."""
    cricket_file = os.path.join(
        os.path.dirname(__file__), '..', 'frontend', 'cricket.html'
    )
    if os.path.exists(cricket_file):
        return FileResponse(cricket_file)
    return {"message": "Welcome to Cricket Statistician AI"}


@app.get("/health")
async def health():
    return {"status": "ok"}
