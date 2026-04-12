# Workout Planner

A workout tracking PWA that recommends weights based on past performance using Brzycki's 1RM formula.

## Features

- **Weight recommendations** -- estimates your 1RM from recent workouts (best of last 4 sessions) and suggests a weight for your target reps/RPE. The recommended weight is editable before logging.
- **Dip-belt support** -- tracks bodyweight separately so added-weight exercises (pull-ups, dips) get accurate 1RM calculations.
- **Progress charts** -- visualise estimated 1RM over time per exercise (powered by Chart.js).
- **Offline mode** -- service worker + IndexedDB let you log workouts offline; pending actions sync when connectivity returns.
- **Multi-user** -- authentication via Cloudflare Access (JWT); each user has isolated data.

## Tech Stack

- **Backend**: FastAPI, SQLModel, SQLite
- **Frontend**: HTMX, Pico CSS, Chart.js
- **Auth**: Cloudflare Access (JWT verification)
- **Infra**: Docker, service worker for offline PWA support

## Running locally

```bash
pip install -r requirements.txt

# Required environment variables for Cloudflare Access auth:
export CF_ACCESS_TEAM_DOMAIN="your-team"
export CF_ACCESS_AUD="your-audience-tag"

uvicorn app:app --reload --host 0.0.0.0 --port 8000
```

## Running with Docker

```bash
docker build -t workout-planner .
docker run -p 8000:8000 \
  -e CF_ACCESS_TEAM_DOMAIN="your-team" \
  -e CF_ACCESS_AUD="your-audience-tag" \
  workout-planner
```

## Project Structure

```
├── app.py           # FastAPI app -- routes, 1RM math, HTML fragments
├── models.py        # SQLModel models (User, Exercise, Workout)
├── database.py      # Engine setup and seed data
├── auth.py          # Cloudflare Access JWT verification
├── index.html       # HTMX frontend
├── sw.js            # Service worker (offline caching & writes)
├── static/          # Icons and offline JS
├── manifest.json    # PWA manifest
├── Dockerfile
└── requirements.txt
```

## API

### Pages
- `GET /` -- main UI

### HTMX endpoints (return HTML fragments)
- `GET /exercises` -- exercise `<option>` list
- `POST /exercises` -- create a new exercise
- `GET /workouts` -- paginated workout history
- `POST /workouts/` -- log a workout
- `GET /bodyweight` -- current bodyweight display
- `PUT /bodyweight` -- update bodyweight
- `POST /recommendations` -- get a weight recommendation

### JSON API
- `GET /progress?exercise_name=...&days=...` -- 1RM history for charting
- `GET /api/sync` -- pull all user data (for offline cache)
- `POST /api/sync` -- replay offline actions
