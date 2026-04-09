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
    
    exercise = session.exec(select(Exercise).where(Exercise.name == exercise_name)).first()
    if not exercise:
        return """
        <div class="no-data-message">
        <p>Exercise not found</p>
        </div>
        """

    bodyweight = 0
    last_bodyweight = 0
    if exercise.dip_belt:
        bodyweight = session.get(User, current_user.id).bodyweight
        last_bodyweight = last_workout.bodyweight if last_workout.bodyweight else bodyweight

    # Calculate 1RM
    onerepmax = (last_workout.weight + last_bodyweight) * 36 / (37 - (last_workout.reps + (10 - last_workout.rpe)))

    # Calculate target
    target_r = reps + (10 - rpe)
    total_weight = onerepmax * (37 - target_r) / 36
    weight = total_weight - bodyweight # bodyweight is 0 when not dip_belt
    weight_rounded = round(weight / 1.25) * 1.25

    return f"""
    <form hx-post="/workouts/"
          hx-target="#workout-table-body"
          hx-swap="afterbegin"
          hx-disinherit="*"
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
                    <td>{format(weight_rounded)}<input type="hidden" name="weight" value="{weight_rounded}"></td>
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
