from contextlib import asynccontextmanager
from pathlib import Path
from typing import List

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
    exercise_name: List[str] = Form(...),
    reps: List[int] = Form(...),
    weight: List[float] = Form(...),
    rpe: List[float] = Form(...)
):
    snippets = []
    for ex, r, w, rp in zip(exercise_name, reps, weight, rpe):
        workout = Workout(
            exercise_name=ex,
            reps=r,
            weight=w,
            rpe=rp,
            user_id=current_user.id
        )
        if session.get(Exercise, ex).dip_belt:
            bodyweight = session.get(User, current_user.id).bodyweight
            workout.bodyweight = bodyweight

        session.add(workout)
        session.commit()
        session.refresh(workout)
        snippets.append(get_workout_row_snippet(workout))

    return ''.join(snippets)

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

@app.get('/add-exercise-row', response_class=HTMLResponse)
def add_exercise_row():
    return """
    <div class="exercise-row form-row">
        <label>
            Exercise
            <select name="exercise_name" required
                    hx-get="/exercises"
                    hx-trigger="load"
                    hx-target="this"
                    hx-swap="innerHTML">
                <option value="">Loading exercises...</option>
            </select>
        </label>
        <label>
            Reps
            <input type="number" name="reps" required min="1" placeholder="e.g., 10">
        </label>
        <label>
            RPE
            <div style="display: flex; align-items: center; gap: 0.5rem;">
                <input type="number" name="rpe" required min="1" max="10" step="0.5" placeholder="1-10">
                <button type="button" class="remove-btn" onclick="if (document.querySelectorAll('#exercise-rows .exercise-row').length > 1) { this.parentElement.parentElement.parentElement.remove(); updateRemoveButtons(); }">Remove</button>
            </div>
        </label>
    </div>
    """

@app.post('/recommendations', response_class=HTMLResponse)
def get_recommendations(*,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
    exercise_name: List[str] = Form(...),
    reps: List[int] = Form(...),
    rpe: List[float] = Form(...)
):
    results = []
    for ex, r, rp in zip(exercise_name, reps, rpe):
        last_workout = session.exec(
            select(Workout)
            .where(Workout.exercise_name == ex, Workout.user_id == current_user.id)
            .order_by(Workout.created_at.desc())
            .limit(1)
        ).first()
        if not last_workout:
            continue  # or return error, but for now skip

        bodyweight = 0
        past_bodyweight = 0
        if last_workout.bodyweight is not None:
            bodyweight = session.get(User, current_user.id).bodyweight
            past_bodyweight = last_workout.bodyweight

        # Calculate 1RM
        onerepmax = (last_workout.weight + past_bodyweight) * 36 / (37 - (last_workout.reps + (10 - last_workout.rpe)))
        # For target
        target_r = r + (10 - rp)
        total_weight = onerepmax * (37 - target_r) / 36
        weight = total_weight - bodyweight
        weight_rounded = round(weight / 1.25) * 1.25
        results.append((ex, r, rp, weight_rounded))

    if not results:
        return """
        <div class="no-data-message">
        <p>No previous data for the selected exercises. Please log workouts first to get recommendations.</p>
        </div>
        """

    table_rows = []
    hidden_inputs = []
    for ex, r, rp, w in results:
        table_rows.append(f"""
        <tr>
            <td>{ex}</td>
            <td>{r}</td>
            <td>{format(rp)}</td>
            <td>{format(w)}</td>
        </tr>
        """)
        hidden_inputs.append(f'<input type="hidden" name="exercise_name" value="{ex}">')
        hidden_inputs.append(f'<input type="hidden" name="reps" value="{r}">')
        hidden_inputs.append(f'<input type="hidden" name="weight" value="{w}">')
        hidden_inputs.append(f'<input type="hidden" name="rpe" value="{rp}">')

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
            {''.join(table_rows)}
        </tbody>
    </table>
    <form hx-post="/workouts/"
          hx-target="#workout-table-body"
          hx-swap="afterbegin"
          hx-disinherit="*"
          hx-on::after-request="if(event.detail.successful) {{ document.getElementById('success-message').innerHTML = '<div class=\'success-message\'>✅ Workouts logged successfully!</div>'; setTimeout(() => document.getElementById('success-message').innerHTML = '', 3000); }}">
        {''.join(hidden_inputs)}
        <button type="submit">Log All</button>
    </form>
    """
