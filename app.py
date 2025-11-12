from contextlib import asynccontextmanager
from pathlib import Path

from auth import create_session_token, hash_password, verify_password, verify_session_token
from models import Exercise, User, Workout
from database import create_db_and_tables, engine

from fastapi import Cookie, Depends, FastAPI, Form, HTTPException, Request, Response, status
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

def get_current_user_id(session_token: str | None = Cookie(None)) -> int:
    if not session_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='Not authenticated'
        )
    
    user_id = verify_session_token(session_token)
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='Invalid session'
        )
    return user_id

@asynccontextmanager
async def lifespan(app: FastAPI):
    create_db_and_tables()
    yield

app = FastAPI(lifespan=lifespan)

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    if exc.status_code == 401 and request.url.path != '/login':
        return RedirectResponse(url='/login', status_code=status.HTTP_303_SEE_OTHER)
    return HTMLResponse(content=str(exc.detail), status_code=exc.status_code)

def get_session():
    with Session(engine) as session:
        yield session

@app.post('/register', response_class=HTMLResponse)
def register(
    *,
    session: Session = Depends(get_session),
    username: str = Form(...),
    password: str = Form(...),
):
    existing = session.exec(select(User).where(User.username == username)).first()
    if existing:
        return '<p style="color: red;">Username already exists</>'

    user = User(
        username=username,
        hashed_password=hash_password(password)
    )
    session.add(user)
    session.commit()
    session.refresh(user)

    return '<p style="color: green;">Registration successful! Please login.</p>'

@app.post('/login', response_class=HTMLResponse)
def login(
    *,
    response: Response,
    session: Session = Depends(get_session),
    username: str = Form(...),
    password: str = Form(...)
):
    user = session.exec(select(User).where(User.username == username)).first()
    if not user or not verify_password(password, user.hashed_password):
        return '<p style="color: red;">Invalid username or password</p>'

    token = create_session_token(user.id)
    response.set_cookie(
        key='session_token',
        value=token,
        httponly=True,
        max_age=86400,
        samesite='lax'
    )

    response.headers['HX-Redirect'] = '/'
    return ''

@app.post('/logout', response_class=HTMLResponse)
def logout(response: Response):
    response.delete_cookie('session_token')
    response.headers['HX-Redirect'] = '/login'
    return ''

@app.get('/login', response_class=HTMLResponse)
def get_login():
    html_path = Path(__file__).parent / 'login.html'
    return html_path.read_text()

@app.get('/', response_class=HTMLResponse)
def get_root(_: int = Depends(get_current_user_id)):
    html_path = Path(__file__).parent / 'index.html'
    return html_path.read_text()

@app.get('/bodyweight', response_class=HTMLResponse)
def get_bodyweight(
    *,
    session: Session=Depends(get_session),
    current_user_id: int = Depends(get_current_user_id)
):
    user = session.get(User, current_user_id)
    return get_bodyweight_snippet(user.bodyweight)

@app.put('/bodyweight', response_class=HTMLResponse)
def put_bodyweight(
    *,
    session: Session=Depends(get_session),
    current_user_id: int = Depends(get_current_user_id),
    bodyweight: float = Form(...)
):
    user = session.get(User, current_user_id)
    user.bodyweight = bodyweight
    session.add(user)
    session.commit()
    session.refresh(user)
    return get_bodyweight_snippet(user.bodyweight)

@app.post('/workouts/', response_class=HTMLResponse)
def create_workout(
    *,
    session: Session = Depends(get_session),
    current_user_id: int = Depends(get_current_user_id),
    exercise_name: str = Form(...), # What does this Form do?
    reps: int = Form(...),
    weight: float = Form(...),
    rpe: float = Form(...)
):
    workout = Workout(
        exercise_name=exercise_name,
        reps=reps,
        weight=weight,
        rpe=rpe,
        user_id=current_user_id
    )
    if session.get(Exercise, exercise_name).dip_belt:
        bodyweight = session.get(User, current_user_id).bodyweight
        workout.bodyweight = bodyweight

    session.add(workout)
    session.commit()
    session.refresh(workout)
    
    return get_workout_row_snippet(workout)

@app.get('/workouts', response_class=HTMLResponse)
def get_workouts(*,
    session: Session = Depends(get_session),
    current_user_id: int = Depends(get_current_user_id)
):
    workouts = session.exec(select(Workout).where(Workout.user_id == current_user_id)).all()
    
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

@app.get('/recommendations', response_class=HTMLResponse)
def get_recommendations(*,
    session: Session = Depends(get_session),
    current_user_id: int = Depends(get_current_user_id),
    exercise_name: str,
    reps: int
):
    last_workout = session.exec(
        select(Workout)
        .where(Workout.exercise_name == exercise_name, Workout.user_id == current_user_id)
        .order_by(Workout.created_at.desc())
        .limit(1)
    ).first()
    if not last_workout:
        return """
        <div class="no-data-message">
        <p>No previous data for this exercise. Please log a workout first to get recommendations.</p>
        </div>
        """
    
    bodyweight, past_bodyweight = 0, 0
    if last_workout.bodyweight is not None:
        bodyweight = session.get(User, current_user_id).bodyweight
        past_bodyweight = last_workout.bodyweight

    # Calculate weights using the Brzycki's formula: https://en.wikipedia.org/wiki/One-repetition_maximum
    # TODO: Handle case where last_workout.reps == 37 (which would cause division by zero)
    recommendations = []
    onerepmax = (last_workout.weight + past_bodyweight) * 36 / (37 - (last_workout.reps + (10 - last_workout.rpe)))
    for rpe in (i * 0.5 for i in range(12, 21)):
        r = reps + (10 - rpe)
        total_weight = onerepmax * (37 - r) / 36 # Weight including body weight
        weight = total_weight - bodyweight
        weight_rounded = round(weight / 1.25) * 1.25  # Lightest plate is 1.25 kg
        recommendations.append((rpe, weight_rounded))
    
    table_rows = []
    for rpe, weight in recommendations:
        table_rows.append(f"""
        <tr>
            <td>{format(rpe)}</td>
            <td>{format(weight)}</td>
            <td>
                <button type="button"
                    onclick="document.getElementById('exercise_name').value=document.getElementById('rec_exercise').value;
                             document.getElementById('reps').value=document.getElementById('rec_reps').value;
                             document.getElementById('rpe').value={rpe};
                             document.getElementById('weight').value={weight};
                             document.getElementById('workout-form').scrollIntoView({{behavior: 'smooth'}});">
                             Select
                </button>
            </td>
        </tr>
        """)
    return f"""
    <table>
        <thead>
            <tr>
                <th>RPE</th>
                <th>Weight (kg)</th>
                <th></th>
            </tr>
        </thead>
        <tbody>
            {''.join(table_rows)}
        </tbody>
    </table>
    """
    