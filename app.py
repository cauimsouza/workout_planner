from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from auth import verify_cf_access_token
from models import Exercise, Movement, User
from database import create_db_and_tables, engine

MIN_REPS = 1
MAX_REPS = 20
MIN_RPE = 6
MAX_RPE = 10
MIN_SETS = 1
MAX_SETS = 10
DEFAULT_SETS = 3
LIGHTEST_PLATE = 1.25

from fastapi import Depends, FastAPI, Form, Header, HTTPException, Query, Request, Response, status
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session, select

def format(number: float) -> str:
    """Format a float to remove unnecessary trailing zeros."""
    return f'{number:g}'

def format_date(date: datetime) -> str:
    if date.year == datetime.now().year:
        return date.strftime("%d %b")
    return date.strftime("%d %b %y")

def get_onerepmax(exercise: Exercise) -> float:
    '''Estimate the external 1RM (excluding bodyweight) using Brzycki's formula.

    For dip-belt movements, this is the added weight the user could
    lift for a single rep. For other movements, this equals the total 1RM.
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
def get_root(_: User = Depends(get_current_user)):
    html_path = Path(__file__).parent / 'index.html'
    return html_path.read_text()

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
    <div id="previous-exercises">
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
    <form hx-post="/exercises/"
          hx-swap="none"
          hx-on::after-request="if(event.detail.successful) {{ var el = this.querySelector('.rec-success'); el.innerHTML = '<div class=\\'success-message\\'>Exercise logged successfully!</div>'; setTimeout(() => el.innerHTML = '', 3000); }}">
        <table>
            <thead>
                <tr>
                    <th>Movement</th>
                    <th>Sets</th>
                    <th>Reps</th>
                    <th>Weight (kg)</th>
                    <th>RPE</th>
                </tr>
            </thead>
            <tbody>
                <tr>
                    <td>{exercise_name}<input type="hidden" name="exercise_name" value="{exercise_name}"></td>
                    <td>
                        <input type="number" name="sets" value="{DEFAULT_SETS}"
                            min="{MIN_SETS}" max="{MAX_SETS}" step="1" style="width: 4rem; margin: 0; padding: 0.25rem;">
                    </td>
                    <td>
                        <input type="number" name="reps" value="{reps}"
                            min="{MIN_REPS}" max="{MAX_REPS}" step="1" style="width: 4rem; margin: 0; padding: 0.25rem;">
                    </td>
                    <td>
                        <input type="number" name="weight" value="{format(weight)}"
                            min="0" step="{LIGHTEST_PLATE}" style="width: 5rem; margin: 0; padding: 0.25rem;">
                    </td>
                    <td>
                        <input type="number" name="rpe" value="{format(rpe)}"
                            min="1" max="10" step="0.5" style="width: 5rem; margin: 0; padding: 0.25rem;">
                    </td>
                </tr>
            </tbody>
        </table>
        <div class="rec-success"></div>
        <button type="submit">Log</button>
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
            movement = session.get(Movement, data['exercise_name'])
            exercise = Exercise(
                exercise_name=data['exercise_name'],
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
            existing = session.exec(select(Movement).where(Movement.name == data['name'])).first()
            if not existing:
                movement = Movement(name=data['name'], dip_belt=bool(data.get('dip_belt', False)))
                session.add(movement)

    session.commit()
    return {"status": "ok", "replayed": len(actions)}

@app.post('/movements', response_class=HTMLResponse)
def create_movement(*,
    response: Response,
    session: Session = Depends(get_session),
    _: User = Depends(get_current_user),
    name: str = Form(...),
    dip_belt: bool = Form(default=False)
):
    movement = session.get(Movement, name)
    if movement:
        return f"""
        <div class="failure-message">
        <p>Movement {name} already exists</p>
        </div>
        """

    movement = Movement(
        name=name,
        dip_belt=dip_belt
    )
    session.add(movement)
    session.commit()

    response.headers["HX-Trigger"] = "movement-created"
    return f"""
    <div class="success-message">
    <p>Movement {name} successfully created</p>
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
        .order_by(Exercise.created_at)
    ).all()

    return {
        'exercise': exercise_name,
        'onerepmax': [
            {
                'date': exercise.created_at.isoformat(),
                'value': get_onerepmax(exercise),
            }
            for exercise in exercises
        ]
    }
