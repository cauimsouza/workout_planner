import html
import re
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from auth import verify_cf_access_token
from models import Exercise, Movement, User, WorkoutDay, WorkoutSession
from movement_names import InvalidMovementName, normalize_movement_name
from movements import add_movement, find_existing, rename_movement, resolve_movement, usage
from database import create_db_and_tables, engine

MIN_REPS = 1
MAX_REPS = 20
MIN_RPE = 6
MAX_RPE = 10
MIN_SETS = 1
MAX_SETS = 10
DEFAULT_SETS = 3
LIGHTEST_PLATE = 1.25
CYCLE_WEEKS = 4
WEEKDAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']

from fastapi import Depends, FastAPI, Form, Header, HTTPException, Query, Request, Response, status
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field as PydanticField
from sqlmodel import Session, col, delete, select

def format(number: float | None) -> str:
    """Format a float to remove unnecessary trailing zeros."""
    return '—' if number is None else f'{number:g}'

def format_date(date: datetime) -> str:
    """Format a date as "4 Nov 2026"."""
    return f"{date.day} {date:%b %Y}"

def get_onerepmax(exercise: Exercise) -> float:
    '''Estimate the external 1RM (excluding bodyweight) using Brzycki's formula.

    For dip-belt movements, this is the added weight the user could
    lift for a single rep. For other movements, this equals the total 1RM.
    The exercise must have an RPE.
    '''
    bodyweight = exercise.bodyweight if exercise.bodyweight else 0
    return (exercise.weight + bodyweight) * 36 / (37 - (exercise.reps + (10 - exercise.rpe))) - bodyweight

def get_target_weight(onerepmax: float, reps: int, current_bodyweight: float | None, rpe: float) -> float:
    '''Calculate the external weight to use for a target reps/RPE.

    onerepmax: external 1RM as returned by get_onerepmax.
    current_bodyweight: user's current bodyweight for dip-belt movements, None otherwise.
    '''
    bw = current_bodyweight if current_bodyweight else 0
    target_r = reps + (10 - rpe)
    total_weight = (onerepmax + bw) * (37 - target_r) / 36
    weight = total_weight - bw
    return round(weight / LIGHTEST_PLATE) * LIGHTEST_PLATE

def get_exercise_row_snippet(exercise: Exercise) -> str:
    return f"""
    <tr>
        <th scope="row">{format_date(exercise.created_at)}
            <button class="delete-btn"
                    hx-post="/exercises/{exercise.id}/delete"
                    hx-confirm="Delete this exercise?"
                    hx-target="#previous-exercises"
                    hx-swap="outerHTML">✕</button>
        </th>
        <td>{exercise.exercise_name}</td>
        <td>{exercise.sets}</td>
        <td>{exercise.reps}</td>
        <td>{format(exercise.weight)}</td>
        <td>{format(exercise.rpe)}</td>
    </tr>
    """

def get_onerepmax_tile(onerepmax: float, bodyweight: float | None) -> str:
    """Estimated 1RM tile for the load calculator. bodyweight is set for dip-belt movements."""
    rounded = round(onerepmax * 2) / 2
    if bodyweight is not None:
        detail = f'added weight · {format(round((onerepmax + bodyweight) * 2) / 2)} kg with bodyweight'
    else:
        detail = 'from your best recent set'
    return f"""
        <div class="tile accent">
            <div class="tile-label">Estimated 1RM</div>
            <div class="tile-value">{format(rounded)} kg</div>
            <div class="tile-sub">{detail}</div>
        </div>
    """

def get_bodyweight_snippet(bodyweight: float) -> str:
    return f"""
    <div id="bodyweight-display">
        <p style="font-size: 0.9rem; color: var(--pico-muted-color); margin-top: 0.5rem;">
            Current: <strong>{format(bodyweight)}</strong>
        </p>
    </div>
    """

def get_session():
    with Session(engine) as session:
        yield session

def get_current_user(
    session: Session = Depends(get_session),
    cf_access_jwt_assertion: str = Header(alias="Cf-Access-Jwt-Assertion"),
) -> User:
    email = verify_cf_access_token(cf_access_jwt_assertion)
    if email is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid Cloudflare Access token",
        )

    user = session.exec(select(User).where(User.email == email)).first()
    if not user:
        user = User(email=email)
        session.add(user)
        session.commit()
        session.refresh(user)
    return user

@asynccontextmanager
async def lifespan(app: FastAPI):
    create_db_and_tables()
    yield

app = FastAPI(lifespan=lifespan)

@app.middleware('http')
async def revalidate_app_files(request: Request, call_next):
    """Make browsers check the page and its files with the server on every load.

    Without a Cache-Control header, browsers may reuse a cached script for a while without asking,
    so after an update the new page could run with old JavaScript. "no-cache" still lets them keep
    the files, but they revalidate first (a cheap 304 when nothing changed).
    """
    response = await call_next(request)
    path = request.url.path
    if path == '/' or path.startswith('/static/') or path in ('/sw.js', '/manifest.json'):
        response.headers.setdefault('Cache-Control', 'no-cache')
    return response

app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/manifest.json")
def get_manifest():
    return FileResponse("manifest.json", media_type="application/manifest+json")

@app.get("/sw.js")
def get_service_worker():
    return FileResponse("sw.js", media_type="application/javascript")

@app.get('/login')
def get_login():
    return RedirectResponse(url='/')

@app.get('/', response_class=HTMLResponse)
def get_root(current_user: User = Depends(get_current_user)):
    html_path = Path(__file__).parent / 'index.html'
    html = html_path.read_text()
    if not current_user.is_admin:
        import re
        html = re.sub(
            r'<!-- BEGIN Create Movement -->.*?<!-- END Create Movement -->',
            '',
            html,
            flags=re.DOTALL,
        )
    return html

@app.get('/bodyweight', response_class=HTMLResponse)
def get_bodyweight(
    *,
    session: Session=Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    user = session.get(User, current_user.id)
    return get_bodyweight_snippet(user.bodyweight)

@app.put('/bodyweight', response_class=HTMLResponse)
def put_bodyweight(
    *,
    session: Session=Depends(get_session),
    current_user: User = Depends(get_current_user),
    bodyweight: float = Form(...)
):
    user = session.get(User, current_user.id)
    user.bodyweight = bodyweight
    session.add(user)
    session.commit()
    session.refresh(user)
    return get_bodyweight_snippet(user.bodyweight)

@app.post('/exercises/', response_class=HTMLResponse)
def create_exercise(
    *,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
    exercise_name: str = Form(...),
    reps: int = Form(..., ge=MIN_REPS, le=MAX_REPS),
    weight: float = Form(...),
    rpe: float = Form(..., ge=MIN_RPE, le=MAX_RPE),
    sets: int = Form(default=DEFAULT_SETS, ge=MIN_SETS, le=MAX_SETS),
    exercise_date: date | None = Form(default=None)
):
    exercise = Exercise(
        exercise_name=exercise_name,
        sets=sets,
        reps=reps,
        weight=weight,
        rpe=rpe,
        user_id=current_user.id
    )
    if exercise_date:
        exercise.created_at = datetime.combine(exercise_date, datetime.now(timezone.utc).time(), tzinfo=timezone.utc)
    if session.get(Movement, exercise_name).dip_belt:
        bodyweight = session.get(User, current_user.id).bodyweight
        exercise.bodyweight = bodyweight

    session.add(exercise)
    session.commit()
    session.refresh(exercise)
    return "<div><p>Exercise logged</p></div>"

# POST instead of DELETE because htmx 1.9 doesn't swap response bodies from DELETE requests.
@app.post('/exercises/{exercise_id}/delete', response_class=HTMLResponse)
def delete_exercise(
    *,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
    exercise_id: int
):
    exercise = session.get(Exercise, exercise_id)
    if not exercise or exercise.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Exercise not found")
    session.delete(exercise)
    session.commit()
    return get_exercises(session=session, current_user=current_user, offset=0, limit=5)

@app.get('/exercises', response_class=HTMLResponse)
def get_exercises(*,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=5, ge=1, le=20)
):
    exercises = session.exec(
        select(Exercise)
        .where(Exercise.user_id == current_user.id)
        .order_by(Exercise.created_at.desc())
        .offset(offset)
        .limit(limit + 1) # Fetch one extra row to determine if a next page exists
    ).all()
    table_rows = [get_exercise_row_snippet(exercise) for exercise in exercises[:limit]]

    def make_button(label:str, offset: int, limit: int) -> str:
        return f"""
            <button hx-get="/exercises?offset={offset}&limit={limit}"
                    hx-target="#previous-exercises"
                    hx-swap="outerHTML">{label}</button>
        """
    previous_button = make_button("Previous", max(offset - limit, 0), limit) if offset > 0 else ""
    next_button = make_button("Next", offset + limit, limit) if len(exercises) > limit else ""

    return f"""
    <div id="previous-exercises"
         hx-get="/exercises"
         hx-trigger="exercise-logged from:body"
         hx-swap="outerHTML">
        <div style="overflow-x: auto">
        <table>
            <thead>
                <tr>
                    <th scope="col">Date</th>
                    <th scope="col">Movement</th>
                    <th scope="col">Sets</th>
                    <th scope="col">Reps</th>
                    <th scope="col">Weight</th>
                    <th scope="col">RPE</th>
                </tr>
            </thead>
            <tbody>{''.join(table_rows)}</tbody>
        </table>
        </div>
        <div class="pagination">
            <div>{previous_button}</div>
            <div class="pagination-next">{next_button}</div>
        </div>
    </div>
    """

@app.get('/movements', response_class=HTMLResponse)
def get_movements(*, session: Session = Depends(get_session)):
    movements = session.exec(select(Movement)).all()

    if not movements:
        return '<option value="">No movements available</option>'

    options = ['<option value="">Select a movement</option>']
    for movement in movements:
        options.append(f'<option value="{movement.name}">{movement.name}</option>')

    return "\n".join(options)

@app.post('/recommendations', response_class=HTMLResponse)
def get_recommendation(*,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
    exercise_name: str = Form(...),
    reps: int = Form(..., ge=MIN_REPS, le=MAX_REPS),
    rpe: float = Form(..., ge=MIN_RPE, le=MAX_RPE)
):
    # Bad days at the gym when we feel weaker than usual are common.
    # To be robust against that and stimulate the user, we use their best
    # performance over the past 4 days.
    LOOKBACK_WINDOW = 4
    exercises = session.exec(
        select(Exercise)
        .where(Exercise.exercise_name == exercise_name, Exercise.user_id == current_user.id)
        .where(col(Exercise.rpe).is_not(None))
        .order_by(Exercise.created_at.desc())
        .limit(LOOKBACK_WINDOW)
    ).all()
    if not exercises:
        return """
        <div class="failure-message">
        <p>No previous data for this movement. Please log an exercise first to get a recommendation.</p>
        </div>
        """
    onerepmax = max(get_onerepmax(exercise) for exercise in exercises)

    movement = session.exec(select(Movement).where(Movement.name == exercise_name)).first()
    if not movement:
        return """
        <div class="failure-message">
        <p>Movement not found</p>
        </div>
        """
    bodyweight = session.get(User, current_user.id).bodyweight if movement.dip_belt else None

    weight = get_target_weight(onerepmax, reps, bodyweight, rpe)
    return f"""
    <div class="tiles">
        {get_onerepmax_tile(onerepmax, bodyweight)}
        <div class="tile">
            <div class="tile-label">Load</div>
            <div class="tile-value">{format(weight)} kg</div>
            <div class="tile-sub">for {reps} reps at RPE {format(rpe)}</div>
        </div>
    </div>
    <form class="calc-log" hx-post="/exercises/"
          hx-swap="none"
          hx-on::after-request="if(event.detail.successful) {{ var el = this.querySelector('.rec-success'); el.innerHTML = '<div class=\\'success-message\\'>Exercise logged successfully!</div>'; setTimeout(() => el.innerHTML = '', 3000); document.body.dispatchEvent(new Event('exercise-logged')); }}">
        <p class="calc-hint">Did this set? Adjust and log it:</p>
        <input type="hidden" name="exercise_name" value="{html.escape(exercise_name, quote=True)}">
        <div class="calc-grid">
            <label>Sets <input type="number" name="sets" value="{DEFAULT_SETS}" min="{MIN_SETS}" max="{MAX_SETS}" step="1" inputmode="numeric"></label>
            <label>Reps <input type="number" name="reps" value="{reps}" min="{MIN_REPS}" max="{MAX_REPS}" step="1" inputmode="numeric"></label>
            <label>Load (kg) <input type="text" name="weight" value="{format(weight)}" required inputmode="decimal" autocomplete="off"></label>
            <label>RPE <input type="text" name="rpe" value="{format(rpe)}" required data-min="{MIN_RPE}" data-max="{MAX_RPE}" inputmode="decimal" autocomplete="off"></label>
        </div>
        <div class="rec-success"></div>
        <button type="submit" class="secondary outline">Log this set</button>
    </form>
    """

@app.get('/api/sync')
def api_sync(*,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    movements = session.exec(select(Movement)).all()
    exercises = session.exec(
        select(Exercise)
        .where(Exercise.user_id == current_user.id)
        .order_by(Exercise.created_at.desc())
    ).all()
    user = session.get(User, current_user.id)
    return {
        "movements": [{"name": m.name, "dip_belt": m.dip_belt} for m in movements],
        "exercises": [
            {
                "id": e.id,
                "exercise_name": e.exercise_name,
                "sets": e.sets,
                "reps": e.reps,
                "weight": e.weight,
                "rpe": e.rpe,
                "bodyweight": e.bodyweight,
                "created_at": e.created_at.isoformat(),
            }
            for e in exercises
        ],
        "bodyweight": user.bodyweight,
    }

@app.post('/api/sync')
async def api_sync_push(*,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    body = await request.json()
    actions = body.get('actions', [])

    for action in actions:
        action_type = action['type']
        data = action['data']

        if action_type == 'create_exercise':
            name = resolve_movement(session, data['exercise_name'])  # It may have been renamed since
            if name is None:
                continue
            movement = session.get(Movement, name)
            exercise = Exercise(
                exercise_name=name,
                sets=int(data.get('sets', DEFAULT_SETS)),
                reps=int(data['reps']),
                weight=float(data['weight']),
                rpe=float(data['rpe']),
                user_id=current_user.id,
            )
            if movement and movement.dip_belt:
                exercise.bodyweight = session.get(User, current_user.id).bodyweight
            if 'created_at' in action:
                exercise.created_at = datetime.fromisoformat(action['created_at'])
            session.add(exercise)

        elif action_type == 'update_bodyweight':
            user = session.get(User, current_user.id)
            user.bodyweight = float(data['bodyweight'])
            session.add(user)

        elif action_type == 'create_movement':
            if not current_user.is_admin:
                continue
            try:
                name = check_new_movement_name(session, data['name'])
            except InvalidMovementName:
                continue  # Invalid, or it already exists
            add_movement(session, name, bool(data.get('dip_belt', False)))
            session.flush()

    session.commit()
    return {"status": "ok", "replayed": len(actions)}

def check_new_movement_name(session: Session, raw_name: str, renaming: str | None = None) -> str:
    """Standard form of a new movement name. Raises InvalidMovementName if it's invalid or already exists.

    When renaming, the movement being renamed doesn't count as existing.
    """
    name = normalize_movement_name(raw_name)
    existing = find_existing(session, name, exclude=renaming)
    if existing is not None:
        raise InvalidMovementName(f'{existing} already exists.')
    return name

@app.get('/api/movements/normalize')
def preview_movement_name(*,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
    name: str = Query(...),
    renaming: str | None = Query(default=None),
):
    """The standard form of a name for a new (or renamed) movement, so it can be shown before saving.

    `existing` names the movement it clashes with: an error when creating, a merge when renaming.
    """
    try:
        standard = normalize_movement_name(name)
    except InvalidMovementName as error:
        return {'name': None, 'error': str(error), 'existing': None}
    existing = find_existing(session, standard, exclude=renaming)
    if existing is not None:
        return {'name': standard, 'error': f'{existing} already exists.', 'existing': existing}
    return {'name': standard, 'error': None, 'existing': None}

class MovementRenameIn(BaseModel):
    old_name: str
    new_name: str
    dip_belt: bool
    merge: bool = False # Confirms merging into an existing movement with the new name

def require_admin(user: User):
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Only admins can change movements.')

@app.get('/api/movements/usage')
def get_movement_usage(*,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
    name: str = Query(...),
):
    """How many logged exercises (all users) and workout days use a movement."""
    require_admin(current_user)
    if session.get(Movement, name) is None:
        raise HTTPException(status_code=404, detail='Movement not found')
    return usage(session, name)

@app.post('/api/movements/rename')
def post_movement_rename(*,
    data: MovementRenameIn,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Rename a movement (and/or change its dip belt setting) everywhere it's used.

    Renaming to the name of another movement merges the two, which must be confirmed with merge=true.
    """
    require_admin(current_user)
    if session.get(Movement, data.old_name) is None:
        raise HTTPException(status_code=404, detail='Movement not found')
    try:
        new_name = normalize_movement_name(data.new_name)
    except InvalidMovementName as error:
        raise HTTPException(status_code=422, detail=str(error))
    existing = find_existing(session, new_name, exclude=data.old_name)
    if existing is not None and not data.merge:
        return {'status': 'confirm_merge', 'name': existing, **usage(session, existing)}
    name = rename_movement(session, data.old_name, new_name,
                           # When merging, keep the existing movement's dip belt setting.
                           dip_belt=None if existing is not None else data.dip_belt)
    session.commit()
    return {'status': 'ok', 'name': name, 'merged': existing is not None}

@app.post('/movements', response_class=HTMLResponse)
def create_movement(*,
    response: Response,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
    name: str = Form(...),
    dip_belt: bool = Form(default=False)
):
    if not current_user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")

    try:
        standard_name = check_new_movement_name(session, name)
    except InvalidMovementName as error:
        return f"""
        <div class="failure-message">
        <p>{html.escape(str(error))}</p>
        </div>
        """

    add_movement(session, standard_name, dip_belt)
    session.commit()

    response.headers["HX-Trigger"] = "movement-created"
    note = '' if standard_name == name.strip() else f' (standardized from “{html.escape(name.strip())}”)'
    return f"""
    <div class="success-message">
    <p>Created {html.escape(standard_name)}{note}</p>
    </div>
    """

@app.get('/progress')
def get_progress(*,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
    exercise_name: str = Query(...),
    days: int = Query(gt=0, le=365)
):
    movement = session.get(Movement, exercise_name)
    if not movement:
        raise HTTPException(status_code=404, detail=f"Movement \'{exercise_name}\' not found")

    start_date = datetime.now(timezone.utc) - timedelta(days=days)
    exercises = session.exec(
        select(Exercise)
        .where(Exercise.user_id == current_user.id)
        .where(Exercise.exercise_name == exercise_name)
        .where(Exercise.created_at >= start_date)
        .where(col(Exercise.rpe).is_not(None))
        .order_by(Exercise.created_at)
    ).all()

    daily_best: dict[date, float] = {}
    for exercise in exercises:
        day = exercise.created_at.date()
        orm = get_onerepmax(exercise)
        if day not in daily_best or orm > daily_best[day]:
            daily_best[day] = orm

    return {
        'exercise': exercise_name,
        'onerepmax': [
            {
                'date': day.isoformat(),
                'value': value,
            }
            for day, value in sorted(daily_best.items())
        ]
    }


# --- Program (workout days) and workout sessions ---

class SlotIn(BaseModel):
    """One exercise of a workout day. The movements are alternatives (e.g. row variations)."""
    movements: list[str] = PydanticField(min_length=1)
    sets: int = PydanticField(ge=MIN_SETS, le=MAX_SETS)
    reps: int = PydanticField(ge=MIN_REPS, le=MAX_REPS)
    rpe_targets: list[float] | None = None # One target per week of the cycle, or None for no RPE

class WorkoutDayIn(BaseModel):
    name: str = PydanticField(min_length=1, max_length=100)
    weekday: int | None = PydanticField(default=None, ge=0, le=len(WEEKDAYS) - 1) # 0 = Monday
    slots: list[SlotIn] = PydanticField(min_length=1)

def validate_slots(session: Session, slots: list[SlotIn]) -> list[dict]:
    """Check movements and RPE targets, and return the slots as stored in WorkoutDay.slots."""
    known = set(session.exec(select(Movement.name)).all())
    result = []
    for number, slot in enumerate(slots, start=1):
        movements = list(dict.fromkeys(name.strip() for name in slot.movements if name.strip())) # Drop blanks and duplicates
        if not movements:
            raise HTTPException(status_code=422, detail=f'Exercise {number}: choose a movement.')
        unknown = [name for name in movements if name not in known]
        if unknown:
            raise HTTPException(status_code=422, detail=f'Exercise {number}: unknown movement "{unknown[0]}".')
        rpe_targets = slot.rpe_targets or None
        if rpe_targets is not None:
            if len(rpe_targets) != CYCLE_WEEKS:
                raise HTTPException(status_code=422, detail=f'Exercise {number}: give an RPE target for each of the {CYCLE_WEEKS} weeks.')
            if any(not MIN_RPE <= rpe <= MAX_RPE for rpe in rpe_targets):
                raise HTTPException(status_code=422, detail=f'Exercise {number}: RPE targets must be between {MIN_RPE} and {MAX_RPE}.')
        result.append({'movements': movements, 'sets': slot.sets, 'reps': slot.reps, 'rpe_targets': rpe_targets})
    return result

def get_user_workout_day(session: Session, user: User, day_id: int) -> WorkoutDay:
    day = session.get(WorkoutDay, day_id)
    if not day or day.user_id != user.id:
        raise HTTPException(status_code=404, detail='Workout day not found')
    return day

def save_workout_day(session: Session, day: WorkoutDay, data: WorkoutDayIn) -> dict:
    name = data.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail='Give the workout day a name.')
    day.slots = validate_slots(session, data.slots)
    day.name = name
    day.weekday = data.weekday
    session.add(day)
    session.commit()
    session.refresh(day)
    return {'status': 'ok', 'id': day.id}

@app.post('/api/workout-days')
def create_workout_day(*,
    data: WorkoutDayIn,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    return save_workout_day(session, WorkoutDay(user_id=current_user.id, name=data.name), data)

@app.post('/api/workout-days/{day_id}')
def update_workout_day(*,
    day_id: int,
    data: WorkoutDayIn,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    return save_workout_day(session, get_user_workout_day(session, current_user, day_id), data)

@app.post('/api/workout-days/{day_id}/delete')
def delete_workout_day(*,
    day_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    day = get_user_workout_day(session, current_user, day_id)
    # Keep the logged sessions; they carry their own copy of the day's name.
    for workout_session in session.exec(select(WorkoutSession).where(WorkoutSession.day_id == day_id)).all():
        workout_session.day_id = None
        session.add(workout_session)
    session.delete(day)
    session.commit()
    return {'status': 'ok'}

class SessionEntryIn(BaseModel):
    movement: str
    sets: int = PydanticField(ge=MIN_SETS, le=MAX_SETS)
    reps: int = PydanticField(ge=MIN_REPS, le=MAX_REPS)
    weight: float
    rpe: float | None = PydanticField(default=None, ge=MIN_RPE, le=MAX_RPE)

class WorkoutSessionIn(BaseModel):
    client_id: str = PydanticField(min_length=1, max_length=64)
    day_id: int
    cycle: int = PydanticField(ge=1)
    week: int = PydanticField(ge=1, le=CYCLE_WEEKS)
    date: date
    entries: list[SessionEntryIn]

def upsert_workout_session(session: Session, user: User, data: WorkoutSessionIn) -> WorkoutSession:
    '''Create or replace a workout session and its exercises. Does not commit.

    The browser sends the whole session every time an exercise is completed,
    so replaying the same request (e.g. after being offline) is harmless.
    '''
    day = get_user_workout_day(session, user, data.day_id)
    movements = {movement.name: movement for movement in session.exec(select(Movement)).all()}
    for entry in data.entries:
        if entry.movement not in movements:
            # The movement may have been renamed since the session was recorded.
            renamed = resolve_movement(session, entry.movement)
            if renamed is None:
                raise HTTPException(status_code=422, detail=f'Unknown movement: {entry.movement}')
            entry.movement = renamed

    workout_session = session.exec(
        select(WorkoutSession).where(WorkoutSession.client_id == data.client_id)
    ).first()
    if workout_session and workout_session.user_id != user.id:
        raise HTTPException(status_code=404, detail='Workout session not found')
    if not workout_session:
        workout_session = WorkoutSession(client_id=data.client_id, user_id=user.id, day_name=day.name,
                                         cycle=data.cycle, week=data.week, date=datetime.now(timezone.utc))
    if workout_session.date.date() != data.date:
        workout_session.date = datetime.combine(data.date, datetime.now(timezone.utc).time(), tzinfo=timezone.utc)
    workout_session.day_id = day.id
    workout_session.day_name = day.name
    workout_session.cycle = data.cycle
    workout_session.week = data.week
    session.add(workout_session)
    session.flush()

    session.exec(delete(Exercise).where(Exercise.session_id == workout_session.id))
    bodyweight = session.get(User, user.id).bodyweight
    for index, entry in enumerate(data.entries):
        session.add(Exercise(
            exercise_name=entry.movement,
            sets=entry.sets,
            reps=entry.reps,
            weight=entry.weight,
            rpe=entry.rpe,
            bodyweight=bodyweight if movements[entry.movement].dip_belt else None,
            user_id=user.id,
            session_id=workout_session.id,
            # Offset by a second per exercise so they sort in the order they were done.
            created_at=workout_session.date + timedelta(seconds=index),
        ))
    return workout_session

def exercise_to_dict(exercise: Exercise, session_client_ids: dict[int, str] | None = None) -> dict:
    return {
        'session_client_id': (session_client_ids or {}).get(exercise.session_id),
        'movement': exercise.exercise_name,
        'sets': exercise.sets,
        'reps': exercise.reps,
        'weight': exercise.weight,
        'rpe': exercise.rpe,
        'bodyweight': exercise.bodyweight,
        'date': exercise.created_at.isoformat(),
    }

@app.get('/api/program')
def get_program(*,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    '''Everything the session wizard needs, including history to suggest loads (works offline once cached).'''
    days = session.exec(
        select(WorkoutDay).where(WorkoutDay.user_id == current_user.id).order_by(WorkoutDay.id)
    ).all()
    workout_sessions = session.exec(
        select(WorkoutSession).where(WorkoutSession.user_id == current_user.id).order_by(WorkoutSession.date)
    ).all()
    exercises = session.exec(
        select(Exercise).where(Exercise.user_id == current_user.id).order_by(Exercise.created_at.desc())
    ).all()

    session_client_ids = {workout_session.id: workout_session.client_id for workout_session in workout_sessions}
    entries_by_session: dict[int, list[dict]] = {}
    recent_by_movement: dict[str, list[dict]] = {}
    for exercise in exercises:
        if exercise.session_id is not None:
            entries_by_session.setdefault(exercise.session_id, []).insert(0, exercise_to_dict(exercise, session_client_ids))
        recent = recent_by_movement.setdefault(exercise.exercise_name, [])
        if len(recent) < 4:
            recent.append(exercise_to_dict(exercise, session_client_ids))

    return {
        'cycle_weeks': CYCLE_WEEKS,
        'lightest_plate': LIGHTEST_PLATE,
        'bodyweight': session.get(User, current_user.id).bodyweight,
        'is_admin': current_user.is_admin,
        'movements': {movement.name: {'dip_belt': movement.dip_belt} for movement in session.exec(select(Movement)).all()},
        'days': [{'id': day.id, 'name': day.name, 'weekday': day.weekday, 'slots': day.slots} for day in days],
        'sessions': [
            {
                'client_id': workout_session.client_id,
                'day_id': workout_session.day_id,
                'day_name': workout_session.day_name,
                'cycle': workout_session.cycle,
                'week': workout_session.week,
                'date': workout_session.date.isoformat(),
                'entries': entries_by_session.get(workout_session.id, []),
            }
            for workout_session in workout_sessions
        ],
        'recent_by_movement': recent_by_movement,
    }

@app.get('/api/trends')
def get_trends(*,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
    days: int = Query(default=180, gt=0, le=730),
):
    '''Per-movement trend for the History sparklines, most recently trained first.

    Uses the daily best estimated 1RM (external, like /progress) when the movement
    has sets with an RPE, otherwise the daily top load.
    '''
    start_date = datetime.now(timezone.utc) - timedelta(days=days)
    exercises = session.exec(
        select(Exercise)
        .where(Exercise.user_id == current_user.id)
        .where(Exercise.created_at >= start_date)
        .order_by(Exercise.created_at)
    ).all()

    by_movement: dict[str, list[Exercise]] = {}
    for exercise in exercises:
        by_movement.setdefault(exercise.exercise_name, []).append(exercise)

    trends = []
    for name, movement_exercises in by_movement.items():
        with_rpe = [exercise for exercise in movement_exercises if exercise.rpe is not None]
        metric = 'e1rm' if with_rpe else 'load'
        daily_best: dict[date, float] = {}
        for exercise in with_rpe or movement_exercises:
            day = exercise.created_at.date()
            value = get_onerepmax(exercise) if metric == 'e1rm' else exercise.weight
            daily_best[day] = max(value, daily_best.get(day, value))
        points = [{'date': day.isoformat(), 'value': round(value, 1)} for day, value in sorted(daily_best.items())]
        trends.append({'movement': name, 'metric': metric, 'points': points})
    trends.sort(key=lambda trend: trend['points'][-1]['date'], reverse=True)
    return {'days': days, 'trends': trends}

@app.post('/api/workout-sessions')
def post_workout_session(*,
    data: WorkoutSessionIn,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    workout_session = upsert_workout_session(session, current_user, data)
    session.commit()
    return {'status': 'ok', 'client_id': workout_session.client_id}

@app.post('/api/workout-sessions/{client_id}/delete')
def delete_workout_session(*,
    client_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    workout_session = session.exec(
        select(WorkoutSession).where(WorkoutSession.client_id == client_id)
    ).first()
    if not workout_session or workout_session.user_id != current_user.id:
        raise HTTPException(status_code=404, detail='Workout session not found')
    session.exec(delete(Exercise).where(Exercise.session_id == workout_session.id))
    session.delete(workout_session)
    session.commit()
    return {'status': 'ok'}
