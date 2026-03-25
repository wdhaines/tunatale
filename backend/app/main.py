"""FastAPI application for TunaTale language learning."""

import logging
from contextlib import asynccontextmanager

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):  # pragma: no cover
    logger.info("TunaTale backend starting up")
    yield
    logger.info("TunaTale backend shutting down")


app = FastAPI(title="TunaTale", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from app.api import audio, curriculum, generation, srs  # noqa: E402

app.include_router(curriculum.router)
app.include_router(generation.router)
app.include_router(srs.router)
app.include_router(audio.router)


@app.get("/api/health")
async def health():
    return {"status": "ok"}
