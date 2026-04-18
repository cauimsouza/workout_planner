from datetime import datetime, timezone
from sqlmodel import Field, SQLModel

class Movement(SQLModel, table=True):
    name: str = Field(primary_key=True)
    dip_belt: bool = Field(default=False)

class ExerciseBase(SQLModel):
    exercise_name: str = Field(index=True, foreign_key='movement.name')
    sets: int = Field(default=3)
    reps: int
    weight: float
    rpe: float

class ExerciseCreate(ExerciseBase):
    pass

class Exercise(ExerciseBase, table=True):
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key='user.id')
    bodyweight: float | None # Only set for movements with dip_belt=True
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class ExercisePublic(ExerciseBase):
    id: int
    created_at: datetime

class User(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    email: str = Field(unique=True)
    bodyweight: float = Field(default=70) # In kg
