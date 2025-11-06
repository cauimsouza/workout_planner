from datetime import datetime, timezone
from sqlmodel import Field, SQLModel

class Exercise(SQLModel, table=True):
    name: str = Field(primary_key=True)

class WorkoutBase(SQLModel):
    exercise_name: str = Field(index=True, foreign_key='exercise.name')
    reps: int
    weight: float
    rpe: float

class WorkoutCreate(WorkoutBase):
    pass

class Workout(WorkoutBase, table=True):
    id: int | None = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class WorkoutPublic(WorkoutBase):
    id: int
    created_at: datetime
