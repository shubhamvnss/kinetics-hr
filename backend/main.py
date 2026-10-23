import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.api.v1.endpoints.to_be_transformation import router as to_be_transformation_router
from app.core.logging import setup_logging
from app.middleware.request_logger import RequestLoggingMiddleware


setup_logging()

frontend_url = os.getenv(
    "FRONTEND_URL",
    "https://kinetic-corporate-loan-cueqftgucxgnc9bv.southindia-01.azurewebsites.net",
).rstrip("/")

app = FastAPI(
    title="Kinetic Analytics API",
    version="1.0.0",
)


app.add_middleware(RequestLoggingMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:8080",
        "http://127.0.0.1:8080",
        frontend_url,
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


app.include_router(
    api_router,
    prefix="/api/v1",
)


@app.get("/health")
def health():
    return {"status": "UP"}