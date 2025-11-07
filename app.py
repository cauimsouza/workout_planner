from contextlib import asynccontextmanager
from pathlib import Path

from models import Exercise, WorkoutCreate, Workout, WorkoutPublic
from database import create_db_and_tables, engine

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse
from sqlmodel import Session, select

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
    
    # Return HTML fragment for HTMX
    return f"""
    <div class="workout-item">
        <div class="workout-header">
            <span class="exercise-name">{workout.exercise_name}</span>
            <span class="workout-created-at">{workout.created_at.strftime('%Y-%m-%d %H:%M')}</span>
        </div>
        <div class="workout-details">
            <div class="detail-item">
                <div class="detail-label">Reps</div>
                <div class="detail-value">{workout.reps}</div>
            </div>
            <div class="detail-item">
                <div class="detail-label">Weight</div>
                <div class="detail-value">{workout.weight} kg</div>
            </div>
            <div class="detail-item">
                <div class="detail-label">RPE</div>
                <div class="detail-value">{workout.rpe}</div>
            </div>
        </div>
    </div>
    """

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
    
    html_items = []
    for workout in reversed(workouts):  # Show newest first
        html_items.append(f"""
        <div class="workout-item">
            <div class="workout-header">
                <span class="exercise-name">{workout.exercise_name}</span>
                <span class="workout-created-at">{workout.created_at.strftime('%Y-%m-%d %H:%M')}</span>
            </div>
            <div class="workout-details">
                <div class="detail-item">
                    <div class="detail-label">Reps</div>
                    <div class="detail-value">{workout.reps}</div>
                </div>
                <div class="detail-item">
                    <div class="detail-label">Weight</div>
                    <div class="detail-value">{workout.weight} kg</div>
                </div>
                <div class="detail-item">
                    <div class="detail-label">RPE</div>
                    <div class="detail-value">{workout.rpe}</div>
                </div>
            </div>
        </div>
        """)
    
    return "\n".join(html_items)

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
            <td class="rpe-cell">{rpe}</td>
            <td class="weight-cell">{weight} kg</td>
        </tr>
        """)
    return f"""
    <table class="recommendation-table">
        <thead>
            <tr>
                <th>RPE</th>
                <th>Recommended Weight</th>
            </tr>
        </thead>
        <tbody>
            {''.join(table_rows)}
        </tbody>
    </table>
    """
    