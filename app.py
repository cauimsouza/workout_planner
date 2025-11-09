from contextlib import asynccontextmanager
from pathlib import Path

from models import Exercise, Workout
from database import create_db_and_tables, engine

from fastapi import Depends, FastAPI, Form
from fastapi.responses import HTMLResponse
from sqlmodel import Session, select

def format(number: float) -> str:
    """Format a float to remove unnecessary trailing zeros."""
    return f'{number:g}'

def get_workout_row(workout: Workout) -> str:
    return f"""
    <tr>
        <th scope="row">{workout.exercise_name}</th>
        <td>{workout.reps}</td>
        <td>{format(workout.weight)}</td>
        <td>{format(workout.rpe)}</td>
    </tr>
    """

@asynccontextmanager
async def lifespan(app: FastAPI):
    create_db_and_tables()
    yield

app = FastAPI(lifespan=lifespan)

def get_session():
    with Session(engine) as session:
        yield session

@app.get('/', response_class=HTMLResponse)
def get_root():
    html_path = Path(__file__).parent / 'index.html'
    return html_path.read_text()

@app.get('/bodyweight', response_class=HTMLResponse)
def get_bodyweight():
    return f"""
    <div id="bodyweight-display">
        <p style="font-size: 0.9rem; color: var(--pico-muted-color); margin-top: 0.5rem;">
            💪 Current: <strong>80 kg</strong> 
        </p>
    </div>
    """

@app.post('/workouts/', response_class=HTMLResponse)
def create_workout(
    *,
    session: Session = Depends(get_session),
    exercise_name: str = Form(...), # What does this Form do?
    reps: int = Form(...),
    weight: float = Form(...),
    rpe: float = Form(...)
):
    workout = Workout(
        exercise_name=exercise_name,
        reps=reps,
        weight=weight,
        rpe=rpe
    )
    session.add(workout)
    session.commit()
    session.refresh(workout)
    
    return get_workout_row(workout)

@app.get('/workouts', response_class=HTMLResponse)
def get_workouts(*, session: Session = Depends(get_session)):
    workouts = session.exec(select(Workout)).all()
    
    if not workouts:
        return """
        <div class="empty-state">
            <div class="empty-state-icon">📝</div>
            <p>No workouts logged yet. Start by adding your first workout!</p>
        </div>
        """
    
    table_rows = []
    for workout in reversed(workouts):  # Show newest first
        table_rows.append(get_workout_row(workout))    

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
        <tbody id="workout-table-body">
            {''.join(table_rows)}
        </tbody>
    </table>
    """

@app.get('/exercises', response_class=HTMLResponse)
def get_exercises(*, session: Session = Depends(get_session)):
    print('Fetching exercises from database')
    exercises = session.exec(select(Exercise)).all()
    
    if not exercises:
        return '<option value="">No exercises available</option>'
    
    options = ['<option value="">Select an exercise</option>']
    for exercise in exercises:
        options.append(f'<option value="{exercise.name}">{exercise.name}</option>')
    
    return "\n".join(options)

@app.get('/recommendations', response_class=HTMLResponse)
def get_recommendations(*, session: Session = Depends(get_session), exercise_name: str, reps: int):
    last_workout = session.exec(
        select(Workout)
        .where(Workout.exercise_name == exercise_name)
        .order_by(Workout.created_at.desc())
        .limit(1)
    ).first()
    if not last_workout:
        return """
        <div class="no-data-message">
        <p>No previous data for this exercise. Please log a workout first to get recommendations.</p>
        </div>
        """
    
    # TODO: Make the body weight configurable
    body_weight = 0
    exercise = session.get(Exercise, exercise_name)
    if exercise.dip_belt:
        body_weight = 84

    # Calculate weights using the Brzycki's formula: https://en.wikipedia.org/wiki/One-repetition_maximum
    # TODO: Handle case where last_workout.reps == 37 (which would cause division by zero)
    recommendations = []
    onerepmax = (last_workout.weight + body_weight) * 36 / (37 - (last_workout.reps + (10 - last_workout.rpe)))
    for rpe in (i * 0.5 for i in range(12, 21)):
        r = reps + (10 - rpe)
        total_weight = onerepmax * (37 - r) / 36 # Weight including body weight
        weight = total_weight - body_weight
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
    