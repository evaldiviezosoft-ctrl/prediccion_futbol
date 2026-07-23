from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import get_settings
from app.core.errors import BackendError
from app.routes import admin, competitions, fixtures, health, predictions, sync
from app.services.scheduler_service import start_scheduler, stop_scheduler

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler = start_scheduler()
    app.state.scheduler = scheduler
    try:
        yield
    finally:
        stop_scheduler(scheduler)


app = FastAPI(title=settings.app_name, version='0.1.0', lifespan=lifespan)


@app.exception_handler(BackendError)
async def backend_error_handler(_request: Request, exc: BackendError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={'detail': exc.public_detail, 'code': exc.code},
    )


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)
app.include_router(health.router)
app.include_router(competitions.router)
app.include_router(fixtures.router)
app.include_router(predictions.router)
app.include_router(admin.router)
app.include_router(sync.router)
