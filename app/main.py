from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import init_db
from app.routers import webhook
from app.routers import reviews

log = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Starting up AI PR Review Assistant...")
    await init_db()
    log.info("Database tables created/verified.")
    yield
    log.info("Shutting down...")


app = FastAPI(
    title="AI PR Review Assistant",
    description="Multi-agent AI system that automatically reviews GitHub Pull Requests",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(webhook.router, prefix="/webhook", tags=["Webhook"])
app.include_router(reviews.router, prefix="/reviews", tags=["Reviews"])


@app.get("/", tags=["Health"])
async def root():
    return {
        "service": "AI PR Review Assistant",
        "status": "running",
        "version": "1.0.0",
        "env": settings.app_env,
        "docs": "/docs",
    }


@app.get("/health", tags=["Health"])
async def health():
    return {"status": "ok"}
