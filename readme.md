# Workout Planner

A self-hosted workout tracker for programs built in **4-week cycles**. You define your workout days
once (e.g. *Primary Pull*: muscle-ups 3×3, weighted chin-ups 3×3, rows 3×10, ...). At the gym, the
app walks you through the day's exercises one at a time and tells you what load to use, based on
what you did last time and the RPE you're aiming for this week.

It's a web app you install on your phone's home screen. It works offline and syncs when you're back
online. It runs on a small server such as a Raspberry Pi, behind Cloudflare Access for login.

- [How it works](#how-it-works)
- [Features](#features)
- [Deploying](#deploying)
- [Running the app](#running-the-app): updates, backups, admins, movement names
- [Development](#development)
- [Architecture](docs/architecture.md) (for changing the code)

## How it works

**Program.** Your program is a set of *workout days*, each usually done on a fixed weekday. A
workout day lists its exercises with sets × reps and, optionally, a target **RPE for each week** of
the cycle (e.g. 7, 7.5, 8, 9: lightest in week 1, heaviest in week 4). An exercise can list
alternative movements, e.g. *Cable seated row* or *Chest supported rows*.

**Cycle and week.** The app tracks where you are without a calendar. The current cycle is the one
you're in until you've done a workout day's week 4; its next session then starts the next cycle. The
week is the furthest week reached so far, or the next one if you've already done this workout day
that week. Missed sessions show up as *skipped* and can still be made up. You can always pick
another week.

**Suggested loads.** For each exercise, the app shows your **reference session** next to the
inputs:

- in weeks 2–4: the same workout day in the previous week of this cycle;
- in week 1: week 1 of the previous cycle;
- otherwise, the most recent time you did that movement.

For exercises with a target RPE, it also **suggests a load**. It estimates your one-rep max from
your best recent set with Brzycki's formula, counting reps in reserve (10 − RPE) as extra reps, then
works back to the load for this week's reps and RPE. Loads are rounded to 1.25 kg. For *dip-belt*
movements (weighted pull-ups, dips, ...) the calculation includes your bodyweight, and the load is
the weight on the belt.

## Features

- **Guided sessions:** one exercise per screen with the target, last time, suggested load, and
  +/− steppers for sets, reps, load and RPE. Back/Skip/Next stay within thumb reach. Each step is
  saved as you go, and a summary at the end compares your loads with the reference session and
  marks new best estimated 1RMs.
- **Program editor:** workout days as cards. Movements come from a searchable list, so names can't
  be mistyped; each exercise has sets × reps and RPE per week. Days can be duplicated, which is
  handy for a new cycle.
- **History:** the current cycle at a glance, logged sessions, a trend line per movement, an
  estimated-1RM chart, and the full exercise log.
- **Extras:** log a one-off exercise outside your program, and a load calculator (estimated 1RM
  and the load for any reps and RPE).
- **Offline:** sessions are saved on the phone and upload when the connection returns.
- **Phone-friendly:** home-screen install (iPhone and Android), light/dark theme, decimal commas
  accepted, no zooming into fields.
- **Multiple users:** each person logged in through Cloudflare Access has their own program and
  history. Movements are shared, and only admins can create them.

## Deploying

The app is a single Docker container with a SQLite database on a Docker volume. It expects
[Cloudflare Access](https://developers.cloudflare.com/cloudflare-one/policies/access/) in front of it:
every request must carry the `Cf-Access-Jwt-Assertion` header that Access adds, and the app
verifies it to identify the user. Without that header, pages are refused.

1. **Cloudflare.** Expose the server through a
   [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/)
   pointing at `http://localhost:8000`, and protect that hostname with an Access application.
   Note the application's **audience (AUD) tag**, and your team name (the `<team>` in
   `<team>.cloudflareaccess.com`).

2. **Configuration.** In the project folder, create `.env`:

   ```bash
   CF_ACCESS_TEAM_DOMAIN=your-team
   CF_ACCESS_AUD=your-audience-tag
   ```

3. **Start.** Run `./start_docker.sh`. It builds the image and (re)starts the `workout-tracker`
   container, restarting it automatically after a reboot. The app listens on `127.0.0.1:8000`
   (reachable only through the tunnel) and keeps its database in the `workout-data` volume at
   `/app/data/database.db`.

4. **First login.** Open the app through your Cloudflare hostname. Users are created the first time
   they log in. [Make yourself an admin](#making-someone-an-admin) so you can create movements,
   then add your workout days in **Program**.

On an iPhone, open the app in Safari and use **Share → Add to Home Screen** to get the full-screen app.

## Running the app

### Updating

```bash
docker stop workout-tracker
docker cp workout-tracker:/app/data/database.db ~/workout-backup-$(date +%F).db   # Backup first
git pull
./start_docker.sh
```

`start_docker.sh` replaces the container but never the `workout-data` volume, so data is kept. If a
new version needs to change the database, it does so on startup, saving a copy as
`database.db.<timestamp>.bak` in the volume first. Check the logs with
`docker logs workout-tracker`. After an update, reload the app on your phone once or twice to get
the new version.

### Backups and restoring

The whole state of the app is one SQLite file. To back it up, stop the container so the copy is
consistent:

```bash
docker stop workout-tracker
docker cp workout-tracker:/app/data/database.db ~/workout-backup-$(date +%F).db
docker start workout-tracker
```

To restore a backup:

```bash
docker cp ~/workout-backup-YYYY-MM-DD.db workout-tracker:/app/data/database.db
docker restart workout-tracker
```

To roll back to an earlier version of the app, check out that commit, run `./start_docker.sh`, and
restore a backup taken before the update. An older version may not understand data written by a
newer one.

### Making someone an admin

Admins can create movements. The person must have logged in once.

```bash
docker exec workout-tracker python -c "
import sqlite3; db = sqlite3.connect('/app/data/database.db')
db.execute(\"UPDATE user SET is_admin = 1 WHERE email = 'you@example.com'\"); db.commit()"
```

### Movement names

Movement names have a standard form (rules in [`movement_names.py`](movement_names.py)):

- sentence case: *Cable seated row* (abbreviations like *RDL* and proper names like *Romanian* keep
  their capitals);
- hyphenated "-up" movements: *Pull-up*, *Chin-up*, *Muscle-up*;
- tidy spacing: *Weighted muscle-up (swing back)*.

New movements are converted automatically ("pull up" becomes *Pull-up*), and a name that matches an
existing movement apart from formatting is refused.

**Renaming a movement.** Admins can fix a typo or shorten a name in **Program → Rename or edit a
movement**: pick the movement, type the new name, and save. Its dip-belt setting can be changed in
the same place. The new name is applied everywhere: every user's logged exercises and every workout
day. Renaming to the name of another movement merges the two, after you confirm. The old name keeps
working for sessions that phones recorded before the rename and upload later.

To bring all older names in line at once:

```bash
docker exec -it workout-tracker python rename_movements.py           # Preview, changes nothing
docker exec -it workout-tracker python rename_movements.py --apply   # Back up, then rename
```

The tool renames each movement everywhere it's used (logged exercises and workout days) and merges
duplicates such as "Pull up" and "Pull-up". It can't change wording, so use
`--rename "Bend Over Rows=Bent-over row"` for that. Movements are shared by all users, so renames
apply to everyone's history.

## Development

Requirements: Python 3.12 (as in the Docker image).

```bash
pip install -r requirements.txt
export CF_ACCESS_TEAM_DOMAIN=your-team CF_ACCESS_AUD=your-audience-tag
export DATABASE_PATH=$PWD/dev.db   # Default is /app/data/database.db (the Docker volume)
uvicorn app:app --reload --port 8000
```

The server starts, but every page needs a valid Cloudflare Access token, so a browser pointed
straight at it gets an error. There is no built-in login bypass. To try the UI locally, run the app
from a small script that replaces the `get_current_user` dependency with a fixed test user
(`app.dependency_overrides[get_current_user] = lambda: user`), and keep that script out of the
deployed code.

There is no automated test suite yet. Changes are checked by driving the app with FastAPI's
`TestClient` and with a headless browser (Playwright, including WebKit for iPhone behaviour).

See **[docs/architecture.md](docs/architecture.md)** for how the code is organised, the data model,
the offline design and the API.
