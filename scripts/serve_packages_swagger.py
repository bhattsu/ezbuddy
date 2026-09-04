"""Dev Swagger for package ingestion + question options (avoids full app.main import)."""

import importlib.util
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.services.aim_factory import shutdown_rds, startup_rds

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _load_router(relative_path: str, module_name: str):
    path = Path(ROOT) / relative_path
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module.router


@asynccontextmanager
async def lifespan(app: FastAPI):
    await startup_rds(app)
    yield
    await shutdown_rds(app)


app = FastAPI(
    title="US Legal Pro — Packages + Question Options (dev)",
    description="ZIP upload → S3 (documents-repo/ + knowledge/) + PostgreSQL.",
    version="1.0.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(RequestValidationError)
async def request_validation_handler(_request, exc: RequestValidationError):
    for err in exc.errors():
        if err.get("loc") == ["body", "file"] and "UploadFile" in str(err.get("msg", "")):
            return JSONResponse(
                status_code=400,
                content={
                    "detail": (
                        "Upload a .zip file using the Choose file button for the file field. "
                        "Remove the default text 'string' before executing."
                    ),
                },
            )
    return JSONResponse(status_code=422, content={"detail": exc.errors()})


app.include_router(
    _load_router(
        "app/api/endpoints/legal_package_ingestion.py",
        "app.api.endpoints.legal_package_ingestion",
    ),
    prefix="/api/packages",
    tags=["Legal Package Ingestion"],
)
app.include_router(
    _load_router(
        "app/api/endpoints/question_options.py",
        "app.api.endpoints.question_options",
    ),
    prefix="/api/questions",
    tags=["Question Options"],
)


@app.get("/")
def root():
    return {"docs": "/docs"}


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(
        "scripts.serve_packages_swagger:app",
        host="127.0.0.1",
        port=port,
        reload=False,
    )
