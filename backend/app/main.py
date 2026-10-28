from __future__ import annotations

from fastapi import FastAPI

from app.routers.health import router as health_router
from app.routers.dedup import router as dedup_router
from app.routers.queries import router as queries_router
from app.routers.records import router as records_router
from app.routers.runs import router as runs_router
from app.routers.screening import router as screening_router
from app.routers.pico import router as pico_router
from app.routers.review import router as review_router

app = FastAPI(title="Mini-Rayyan", version="0.1.0")

app.include_router(health_router)
app.include_router(dedup_router)
app.include_router(queries_router)
app.include_router(records_router)
app.include_router(runs_router)
app.include_router(screening_router)
app.include_router(pico_router)
app.include_router(review_router)


@app.get("/")
async def root() -> dict[str, str]:
    return {"service": "Mini-Rayyan", "status": "ok"}
