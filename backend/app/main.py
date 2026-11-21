from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.settings import settings

app = FastAPI(title="ChartAnalyzer Next API", version="0.1.0", docs_url="/docs" if settings.environment == "development" else None, redoc_url=None)
if settings.environment == "development":
    app.add_middleware(CORSMiddleware, allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"], allow_credentials=True, allow_methods=["GET", "POST"], allow_headers=["Content-Type", "X-CSRF-Token"])
@app.get("/api/v1/health", tags=["system"])
def health() -> dict[str, str]:
    return {"status": "ok", "service": "chartanalyzer-next"}
