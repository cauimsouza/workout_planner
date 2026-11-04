"""Renaming movements, and finding a movement from a name that may be out of date.

Shared by the app (Program > Edit a movement) and rename_movements.py.
"""
from sqlmodel import Session, select

from models import Exercise, Movement, MovementAlias, WorkoutDay
from movement_names import InvalidMovementName, normalize_movement_name


def find_existing(session: Session, name: str, exclude: str | None = None) -> str | None:
    """The movement whose name has the same standard form as `name` (e.g. "Pull up" for "Pull-up")."""
    target = name.casefold()
    for existing in session.exec(select(Movement.name)).all():
        if existing == exclude:
            continue
        try:
            standard = normalize_movement_name(existing)
        except InvalidMovementName:
            standard = existing
        if standard.casefold() == target or existing.casefold() == target:
            return existing
    return None


def rename_movement(session: Session, old_name: str, new_name: str, dip_belt: bool | None = None) -> str:
    """Rename a movement everywhere it's used. Does not commit.

    new_name must already be in standard form. If another movement has that name, the two are
    merged and the existing one's dip belt setting is kept (unless dip_belt is given). Logged
    exercises (all users) and workout days are updated, and the old name is remembered as an alias.
    Returns the name the movement ends up with.
    """
    movement = session.get(Movement, old_name)
    if movement is None:
        raise KeyError(old_name)

    target_name = find_existing(session, new_name, exclude=old_name) or new_name
    if target_name == old_name:
        if dip_belt is not None:
            movement.dip_belt = dip_belt
            session.add(movement)
        return old_name

    target = session.get(Movement, target_name)
    if target is None:
        target = Movement(name=target_name, dip_belt=movement.dip_belt if dip_belt is None else dip_belt)
        session.add(target)
    elif dip_belt is not None:
        target.dip_belt = dip_belt
    session.flush()

    for exercise in session.exec(select(Exercise).where(Exercise.exercise_name == old_name)).all():
        exercise.exercise_name = target_name
        session.add(exercise)

    for day in session.exec(select(WorkoutDay)).all():
        if not any(old_name in slot['movements'] for slot in day.slots):
            continue
        day.slots = [  # Reassign so the JSON column is saved
            {**slot, 'movements': list(dict.fromkeys(target_name if name == old_name else name for name in slot['movements']))}
            for slot in day.slots
        ]
        session.add(day)

    # Remember the old name, and keep aliases pointing at the old name working.
    for alias in session.exec(select(MovementAlias).where(MovementAlias.movement_name == old_name)).all():
        alias.movement_name = target_name
        session.add(alias)
    stale = session.get(MovementAlias, target_name)  # The new name was once an alias itself
    if stale is not None:
        session.delete(stale)
    existing_alias = session.get(MovementAlias, old_name)
    if existing_alias is None:
        session.add(MovementAlias(old_name=old_name, movement_name=target_name))
    else:
        existing_alias.movement_name = target_name

    session.flush()
    session.delete(movement)
    session.flush()
    return target_name


def add_movement(session: Session, name: str, dip_belt: bool) -> Movement:
    """Create a movement (name already checked and in standard form). Does not commit."""
    movement = Movement(name=name, dip_belt=dip_belt)
    session.add(movement)
    alias = session.get(MovementAlias, name)  # A former name reused for a new movement
    if alias is not None:
        session.delete(alias)
    return movement


def resolve_movement(session: Session, name: str) -> str | None:
    """The current name of the movement `name` refers to: itself, a former name, or another spelling."""
    if session.get(Movement, name) is not None:
        return name
    alias = session.get(MovementAlias, name)
    if alias is not None:
        return alias.movement_name
    try:
        return find_existing(session, normalize_movement_name(name))
    except InvalidMovementName:
        return None


def usage(session: Session, name: str) -> dict:
    """How much a movement is used, to show before renaming it."""
    exercises = len(session.exec(select(Exercise.id).where(Exercise.exercise_name == name)).all())
    days = sum(1 for day in session.exec(select(WorkoutDay)).all()
               if any(name in slot['movements'] for slot in day.slots))
    return {'exercises': exercises, 'workout_days': days}
