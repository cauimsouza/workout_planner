# Workout Planner

A workout tracking PWA that recommends weights based on past performance using Brzycki's 1RM formula.

## Features

- **Guided workout sessions** -- define your program as workout days (e.g. "Primary Pull") in 4-week cycles. The Workout section walks you through a day's exercises one at a time. It suggests the cycle/week and prefills each load from your reference session: the previous week of the cycle, or week 1 of the previous cycle. For exercises with a target RPE, it also computes a weight for that RPE. Every step is saved immediately, works offline, and appears in Sessions.
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
├── models.py        # SQLModel models (User, Movement, Exercise, WorkoutDay, WorkoutSession)
├── movement_names.py  # Standard form for movement names
├── rename_movements.py  # One-off tool: rename existing movements to the standard form
├── database.py      # Engine setup and seed data
├── auth.py          # Cloudflare Access JWT verification
├── index.html       # HTMX frontend
├── sw.js            # Service worker (offline caching & writes)
├── static/          # Icons, font, offline JS, session wizard (workout.js), program editor (program.js)
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
- `GET /api/program` -- workout days, logged sessions and recent loads (for the session wizard and program editor)
- `POST /api/workout-days`, `POST /api/workout-days/{id}`, `POST /api/workout-days/{id}/delete` -- create/update/delete a workout day (JSON)
- `GET /api/trends?days=...` -- per-movement trend (estimated 1RM or top load) for History
- `POST /api/workout-sessions` -- create or replace a workout session (idempotent, keyed by `client_id`)
- `POST /api/workout-sessions/{client_id}/delete` -- delete a workout session

### Program editor

Workout days are edited in the Program tab: one card per exercise, with the movement picked from a
searchable list (alternatives such as row variations can be added), sets x reps, and optional RPE
targets, one per week of the 4-week cycle.

### Movement names

Movement names have a standard form (rules in `movement_names.py`): sentence case ("Cable seated row"),
hyphenated "-up" movements ("Pull-up", "Muscle-up"), and tidy spacing. New movements are converted
automatically, and a name that matches an existing movement apart from formatting is refused.

To bring existing movements in line, run `rename_movements.py` (with Docker:
`docker exec -it workout-tracker python rename_movements.py`). It only shows the planned changes
until you add `--apply`, which backs up the database first, renames each movement in logged
exercises and workout days, and merges duplicates such as "Pull up" and "Pull-up". Use
`--rename "Old=New"` for wording changes the rules can't make, e.g. `--rename "Bend Over Rows=Bent-over row"`.

### Database migrations

On startup, an `exercise` table from an older version is rebuilt to make `rpe` optional and add `session_id`.
A copy of the database file is saved next to it first (`database.db.<timestamp>.bak`).
`DATABASE_PATH` overrides the database location (default `/app/data/database.db`).
