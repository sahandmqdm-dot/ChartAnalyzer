# ChartAnalyzer Next

Security-first quantitative research foundation.

## Local setup
1. Copy `.env.example` to `.env`.
2. Set a random `APP_SECRET` of 32+ characters and a strong `ADMIN_PASSWORD`.
3. Install backend dependencies and start the API:

```sh
cd backend
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# Linux/macOS: source .venv/bin/activate
pip install -e '.[dev]'
uvicorn app.main:app --reload
```

Health endpoint: http://127.0.0.1:8000/api/v1/health
Development API docs: http://127.0.0.1:8000/docs

## Current implementation
- FastAPI backend and typed configuration
- Local bootstrap admin login
- Signed session cookie, CSRF token, logout invalidation (single-process memory)
- Basic login attempt throttling
- OHLCV candle domain schema with UTC normalization and OHLC invariants
- Candle-series timestamp quality validation
- React + TypeScript + Vite scaffold
- Docker Compose and CI checks

## Security status
This is an early development scaffold. Authentication is a single configured admin account. Throttle/session revocation are in-memory and reset on process restart; multi-process deployment is not supported. Do not expose to the internet or use for trading. Database-backed users/sessions, persistent throttling, complete authorization, audit trail, hardened deployment, and security review are still pending.
