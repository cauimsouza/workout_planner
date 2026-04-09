from contextlib import asynccontextmanager
from pathlib import Path

from auth import verify_cf_access_token
from models import Exercise, User, Workout
from database import create_db_and_tables, engine

from fastapi import Depends, FastAPI, Form, Header, HTTPException, Request, status
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session, select

def format(number: float) -> str:
    """Format a float to remove unnecessary trailing zeros."""
    return f'{number:g}'

def get_workout_row_snippet(workout: Workout) -> str:
    return f"""
    <tr>
        <th scope="row">{workout.exercise_name}</th>
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
    reps: int = Form(...),
    weight: float = Form(...),
    rpe: float = Form(...)
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
    return get_workout_row_snippet(workout)

@app.get('/workouts', response_class=HTMLResponse)
def get_workouts(*,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    workouts = session.exec(select(Workout).where(Workout.user_id == current_user.id)).all()

    table_rows = []
    for workout in reversed(workouts):  # Show newest first
        table_rows.append(get_workout_row_snippet(workout))

    return f"""
    <table>
        <thead>
            <tr>
                <th scope="col">Exercise</th>
                <th scope="col">Reps</th>
                <th scope="col">Weight (kg)</th>
                <th scope="col">RPE</th>
            </tr>
        </thead>
        <tbody id="workout-table-body">{''.join(table_rows)}</tbody>
    </table>
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
    reps: int = Form(...),
    rpe: float = Form(...)
):
    last_workout = session.exec(
        select(Workout)
        .where(Workout.exercise_name == exercise_name, Workout.user_id == current_user.id)
        .order_by(Workout.created_at.desc())
        .limit(1)
    ).first()

    if not last_workout:
        return """
        <div class="no-data-message">
        <p>No previous data for this exercise. Please log a workout first to get a recommendation.</p>
        </div>
        """

    bodyweight = 0
    past_bodyweight = 0
    if last_workout.bodyweight is not None:
        bodyweight = session.get(User, current_user.id).bodyweight
        past_bodyweight = last_workout.bodyweight

    # Calculate 1RM
    onerepmax = (last_workout.weight + past_bodyweight) * 36 / (37 - (last_workout.reps + (10 - last_workout.rpe)))
    # For target
    target_r = reps + (10 - rpe)
    total_weight = onerepmax * (37 - target_r) / 36
    weight = total_weight - bodyweight
    weight_rounded = round(weight / 1.25) * 1.25

    return f"""
    <table>
        <thead>
            <tr>
                <th>Exercise</th>
                <th>Reps</th>
                <th>RPE</th>
                <th>Weight (kg)</th>
            </tr>
        </thead>
        <tbody>
            <tr>
                <td>{exercise_name}</td>
                <td>{reps}</td>
                <td>{format(rpe)}</td>
                <td>{format(weight_rounded)}</td>
            </tr>
        </tbody>
    </table>
    <form hx-post="/workouts/"
          hx-target="#workout-table-body"
          hx-swap="afterbegin"
          hx-disinherit="*"
          hx-on::after-request="if(event.detail.successful) {{ document.getElementById('success-message').innerHTML = '<div class=\\'success-message\\'>Workout logged successfully!</div>'; setTimeout(() => document.getElementById('success-message').innerHTML = '', 3000); }}">
        <input type="hidden" name="exercise_name" value="{exercise_name}">
        <input type="hidden" name="reps" value="{reps}">
        <input type="hidden" name="weight" value="{weight_rounded}">
        <input type="hidden" name="rpe" value="{rpe}">
        <button type="submit">Log</button>
    </form>
    """
