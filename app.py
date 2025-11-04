from contextlib import asynccontextmanager

from .models import Exercise, WorkoutCreate, Workout, WorkoutPublic
from .database import create_db_and_tables, engine

from fastapi import Depends, FastAPI
from sqlmodel import Session, select

@asynccontextmanager
async def lifespan(app: FastAPI):
    create_db_and_tables()
    yield

app = FastAPI(lifespan=lifespan)

def get_session():
    with Session(engine) as session:
        yield session

@app.get('/')
def get_root():
    return {'message': 'Hello world'}

@app.post('/workouts/', response_model=WorkoutPublic)
def create_session(*, session = Depends(get_session), workout: WorkoutCreate):
    workout_db = Workout.model_validate(workout)
    session.add(workout_db)
    session.commit()
    session.refresh(workout_db)
    return workout_db

@app.get('/workouts', response_model=list[WorkoutPublic])
def get_workouts(*, session = Depends(get_session)):
    return session.exec(select(Workout)).all()

@app.get('/exercises', response_model=list[Exercise])
def get_exercises(*, session = Depends(get_session)):
    return session.exec(select(Exercise)).all()
