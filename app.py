from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from auth import verify_cf_access_token
from models import Exercise, User, Workout
from database import create_db_and_tables, engine

MIN_REPS = 1
MAX_REPS = 20
MIN_RPE = 6
MAX_RPE = 10
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

def get_onerepmax(workout: Workout) -> float:
    '''Estimate the external 1RM (excluding bodyweight) using Brzycki's formula.

    For dip-belt exercises, this is the added weight the user could
    lift for a single rep. For other exercises, this equals the total 1RM.
    '''
    bodyweight = workout.bodyweight if workout.bodyweight else 0
    return (workout.weight + bodyweight) * 36 / (37 - (workout.reps + (10 - workout.rpe))) - bodyweight

def get_target_weight(onerepmax: float, reps: int, current_bodyweight: float | None, rpe: float) -> float:
    '''Calculate the external weight to use for a target reps/RPE.

    onerepmax: external 1RM as returned by get_onerepmax.
    current_bodyweight: user's current bodyweight for dip-belt exercises, None otherwise.
    '''
    bw = current_bodyweight if current_bodyweight else 0
    target_r = reps + (10 - rpe)
    total_weight = (onerepmax + bw) * (37 - target_r) / 36
    weight = total_weight - bw
    return round(weight / LIGHTEST_PLATE) * LIGHTEST_PLATE

def get_workout_row_snippet(workout: Workout) -> str:
    return f"""
    <tr>
        <th scope="row">{format_date(workout.created_at)}</th>
        <td>{workout.exercise_name}</td>
        <td>{workout.reps}</td>
        <td>{format(workout.weight)}</td>
        <td>{format(workout.rpe)}</td>
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

@app.post('/workouts/', response_class=HTMLResponse)
def create_workout(
    *,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
    exercise_name: str = Form(...),
    reps: int = Form(..., ge=MIN_REPS, le=MAX_REPS),
    weight: float = Form(...),
    rpe: float = Form(..., ge=MIN_RPE, le=MAX_RPE)
):
    workout = Workout(
        exercise_name=exercise_name,
        reps=reps,
        weight=weight,
        rpe=rpe,
        user_id=current_user.id
    )
    if session.get(Exercise, exercise_name).dip_belt:
        bodyweight = session.get(User, current_user.id).bodyweight
        workout.bodyweight = bodyweight

    session.add(workout)
    session.commit()
    session.refresh(workout)
    return "<div><p>Workout created</p></div>"

@app.get('/workouts', response_class=HTMLResponse)
def get_workouts(*,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=5, ge=1, le=20)
):
    workouts = session.exec(
        select(Workout)
        .where(Workout.user_id == current_user.id)
        .order_by(Workout.created_at.desc())
        .offset(offset)
        .limit(limit + 1) # Fetch one extra row to determine if a next page exists
    ).all()
    table_rows = [get_workout_row_snippet(workout) for workout in workouts[:limit]]

    def make_button(label:str, offset: int, limit: int) -> str:
        return f"""
            <button hx-get="/workouts?offset={offset}&limit={limit}"
                    hx-target="#previous-workouts"
                    hx-swap="outerHTML">{label}</button>
        """
    previous_button = make_button("Previous", max(offset - limit, 0), limit) if offset > 0 else ""
    next_button = make_button("Next", offset + limit, limit) if len(workouts) > limit else ""

    return f"""
    <div id="previous-workouts">
        <div style="overflow-x: auto">
        <table>
            <thead>
                <tr>
                    <th scope="col">Date</th>
                    <th scope="col">Ex.</th>
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

@app.get('/exercises', response_class=HTMLResponse)
def get_exercises(*, session: Session = Depends(get_session)):
    exercises = session.exec(select(Exercise)).all()

    if not exercises:
        return '<option value="">No exercises available</option>'

    options = ['<option value="">Select an exercise</option>']
    for exercise in exercises:
        options.append(f'<option value="{exercise.name}">{exercise.name}</option>')

    return "\n".join(options)

@app.post('/recommendations', response_class=HTMLResponse)
def get_recommendation(*,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
    exercise_name: str = Form(...),
    reps: int = Form(..., ge=MIN_REPS, le=MAX_REPS),
    rpe: float = Form(..., ge=MIN_RPE, le=MAX_RPE)
):
    last_workout = session.exec(
        select(Workout)
        .where(Workout.exercise_name == exercise_name, Workout.user_id == current_user.id)
        .order_by(Workout.created_at.desc())
        .limit(1)
    ).first()

    if not last_workout:
        return """
        <div class="failure-message">
        <p>No previous data for this exercise. Please log a workout first to get a recommendation.</p>
        </div>
        """
    
    exercise = session.exec(select(Exercise).where(Exercise.name == exercise_name)).first()
    if not exercise:
        return """
        <div class="failure-message">
        <p>Exercise not found</p>
        </div>
        """

    onerepmax = get_onerepmax(last_workout)
    bodyweight = session.get(User, current_user.id).bodyweight if exercise.dip_belt else None
    weight = get_target_weight(onerepmax, reps, bodyweight, rpe)
    return f"""
    <form hx-post="/workouts/"
          hx-swap="none"
          hx-on::after-request="if(event.detail.successful) {{ var el = this.querySelector('.rec-success'); el.innerHTML = '<div class=\\'success-message\\'>Workout logged successfully!</div>'; setTimeout(() => el.innerHTML = '', 3000); }}">
        <table>
            <thead>
                <tr>
                    <th>Exercise</th>
                    <th>Reps</th>
                    <th>Weight (kg)</th>
                    <th>RPE</th>
                </tr>
            </thead>
            <tbody>
                <tr>
                    <td>{exercise_name}<input type="hidden" name="exercise_name" value="{exercise_name}"></td>
                    <td>{reps}<input type="hidden" name="reps" value="{reps}"></td>
                    <td>{format(weight)}<input type="hidden" name="weight" value="{weight}"></td>
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
    exercises = session.exec(select(Exercise)).all()
    workouts = session.exec(
        select(Workout)
        .where(Workout.user_id == current_user.id)
        .order_by(Workout.created_at.desc())
    ).all()
    user = session.get(User, current_user.id)
    return {
        "exercises": [{"name": e.name, "dip_belt": e.dip_belt} for e in exercises],
        "workouts": [
            {
                "id": w.id,
                "exercise_name": w.exercise_name,
                "reps": w.reps,
                "weight": w.weight,
                "rpe": w.rpe,
                "bodyweight": w.bodyweight,
                "created_at": w.created_at.isoformat(),
            }
            for w in workouts
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

        if action_type == 'create_workout':
            exercise = session.get(Exercise, data['exercise_name'])
            workout = Workout(
                exercise_name=data['exercise_name'],
                reps=int(data['reps']),
                weight=float(data['weight']),
                rpe=float(data['rpe']),
                user_id=current_user.id,
            )
            if exercise and exercise.dip_belt:
                workout.bodyweight = session.get(User, current_user.id).bodyweight
            if 'created_at' in action:
                workout.created_at = datetime.fromisoformat(action['created_at'])
            session.add(workout)

        elif action_type == 'update_bodyweight':
            user = session.get(User, current_user.id)
            user.bodyweight = float(data['bodyweight'])
            session.add(user)

        elif action_type == 'create_exercise':
            existing = session.exec(select(Exercise).where(Exercise.name == data['name'])).first()
            if not existing:
                exercise = Exercise(name=data['name'], dip_belt=bool(data.get('dip_belt', False)))
                session.add(exercise)

    session.commit()
    return {"status": "ok", "replayed": len(actions)}

@app.post('/exercises', response_class=HTMLResponse)
def create_exercise(*,
    response: Response,
    session: Session = Depends(get_session),
    _: User = Depends(get_current_user),
    name: str = Form(...),
    dip_belt: bool = Form(default=False)
):
    exercise = session.get(Exercise, name)
    if exercise:
        return f"""
        <div class="failure-message">
        <p>Exercise {name} already exists</p>
        </div>
        """
    
    exercise = Exercise(
        name=name,
        dip_belt=dip_belt
    )
    session.add(exercise)
    session.commit()

    response.headers["HX-Trigger"] = "exercise-created"
    return f"""
    <div class="success-message">
    <p>Exercise {name} successfully created</p>
    </div>
    """

@app.get('/progress')
def get_progress(*,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
    exercise_name: str = Query(...),
    days: int = Query(gt=0, le=365)
):
    exercise = session.get(Exercise, exercise_name)
    if not exercise:
        raise HTTPException(status_code=404, detail=f"Exercise \'{exercise_name}\' not found")
    
    start_date = datetime.now(timezone.utc) - timedelta(days=days)
    workouts = session.exec(
        select(Workout)
        .where(Workout.user_id == current_user.id)
        .where(Workout.exercise_name == exercise_name)
        .where(Workout.created_at >= start_date)
        .order_by(Workout.created_at)
    ).all()

    return {
        'exercise': exercise_name,
        'onerepmax': [
            {
                'date': workout.created_at.isoformat(),
                'value': get_onerepmax(workout),
            }
            for workout in workouts
        ]
    }
