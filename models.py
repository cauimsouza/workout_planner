from datetime import datetime, timezone
from sqlmodel import Field, SQLModel

class Exercise(SQLModel, table=True):
    name: str = Field(primary_key=True)
    dip_belt: bool = Field(default=False)

class WorkoutBase(SQLModel):
    exercise_name: str = Field(index=True, foreign_key='exercise.name')
    reps: int
    weight: float
    rpe: float

class WorkoutCreate(WorkoutBase):
    pass

class Workout(WorkoutBase, table=True):
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key='user.id')
    bodyweight: float | None # Only set for exercises with dip_belt=True
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class WorkoutPublic(WorkoutBase):
    id: int
    created_at: datetime

class User(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    username: str = Field(unique=True)
    hashed_password: str
    bodyweight: float = Field(default=70) # In kg