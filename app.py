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
            <span class="workout-id">#{workout.id}</span>
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
