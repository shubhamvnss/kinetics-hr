# Repository Instructions

## Architecture

- The FastAPI app is [backend/main.py](backend/main.py); API routes are mounted under `/api/v1` in [backend/app/api/v1/router.py](backend/app/api/v1/router.py). Keep endpoint handlers thin, put business logic in `backend/app/services/`, and database access in `backend/app/repositories/`. See [tier1_health.py](backend/app/api/v1/endpoints/tier1_health.py) and [tier1_health_service.py](backend/app/services/tier1_health_service.py) for the pattern.
- The React/Vite app is in `frontend/src/`. Keep HTTP calls in [client.js](frontend/src/api/client.js), routing in [App.jsx](frontend/src/App.jsx), and feature views under `frontend/src/pages/`.
- The API reads PostgreSQL settings from environment variables; see [config.py](backend/app/core/config.py) and [.env.example](.env.example). Local API calls that access stored KPI data need valid database settings. Keep credentials in `.env`, never in source.

## Run and Verify

- Frontend development: from `frontend/`, run `npm ci` and `npm run dev` (Vite on port `5173`). Verify frontend changes with `npm run build`.
- Backend development: from the repository root, install `backend/requirements.txt`, then run `uvicorn main:app --app-dir backend --reload --port 8001`. The root working directory lets settings load from the root `.env`.
- Docker Compose runs the frontend on port `8080` and backend on `8001`: `docker compose up --build`.
- No test or lint scripts are currently configured. Do not claim tests passed unless a relevant check has actually been run.

## Project-Specific Notes

- [frontend/README.md](frontend/README.md) contains useful UI descriptions, but its JSON-pipeline and port-`8000` setup instructions are stale. Current dashboard data comes from the API, and Vite proxies `/api` and `/health` to `localhost:8001` in [vite.config.js](frontend/vite.config.js). Verify runtime wiring in code before relying on the README for setup details.
- Compose sets `BACKEND_PROXY_URL` to port `8000`, while the current local Nginx config proxies to backend port `8001`. The entrypoint substitution only matches the `8000` URL, so that variable currently does not rewrite the `8001` target. Check [docker-compose.yml](docker-compose.yml), [nginx.local.conf](frontend/nginx.local.conf), and [container-entrypoint.sh](frontend/container-entrypoint.sh) together when changing container proxy behavior.