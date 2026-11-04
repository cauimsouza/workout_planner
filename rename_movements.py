"""Rename existing movements to the standard form (see movement_names.py).

    python rename_movements.py                    # Show what would change (nothing is written)
    python rename_movements.py --apply            # Back up the database, then rename
    python rename_movements.py --rename "Bend over rows=Bent-over row"   # Also change wording

With Docker:  docker exec -it workout-tracker python rename_movements.py [--apply]

Each movement is renamed everywhere it's used: logged exercises and workout days. Movements
whose names become the same (e.g. "Pull up" and "Pull-up") are merged into one.
"""
import argparse
import shutil
import sys
from collections import defaultdict
from datetime import datetime

from sqlmodel import Session, select

import database
from models import Exercise, Movement, WorkoutDay
from movement_names import InvalidMovementName, normalize_movement_name


def plan_renames(movements: list[Movement], overrides: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    """Map each movement name to its new name. Returns (renames, problems)."""
    renames, problems = {}, []
    for movement in movements:
        try:
            wanted = overrides.get(movement.name, movement.name)
            renames[movement.name] = normalize_movement_name(wanted)
        except InvalidMovementName as error:
            problems.append(f'"{movement.name}": {error} Use --rename "{movement.name}=New name".')
            renames[movement.name] = movement.name
    # Merge into an existing movement when only capitalisation differs.
    canonical = {}
    for old, new in renames.items():
        renames[old] = canonical.setdefault(new.casefold(), new)
    return renames, problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--apply', action='store_true', help='Write the changes (after backing up the database).')
    parser.add_argument('--rename', action='append', default=[], metavar='OLD=NEW',
                        help='Use NEW (standardized) instead of the automatic name for OLD. Repeatable.')
    args = parser.parse_args()

    overrides = {}
    for item in args.rename:
        old, sep, new = item.partition('=')
        if not sep or not old.strip() or not new.strip():
            parser.error(f'--rename needs OLD=NEW, got "{item}"')
        overrides[old.strip()] = new.strip()

    database.engine.echo = False
    with Session(database.engine) as session:
        movements = session.exec(select(Movement)).all()
        unknown = [old for old in overrides if old not in {m.name for m in movements}]
        if unknown:
            print(f'No movement called: {", ".join(unknown)}')
            return 1
        renames, problems = plan_renames(movements, overrides)
        changes = {old: new for old, new in renames.items() if old != new}
        dip_belt = {m.name: m.dip_belt for m in movements}

        exercise_counts = defaultdict(int)
        for exercise in session.exec(select(Exercise)).all():
            exercise_counts[exercise.exercise_name] += 1
        days = session.exec(select(WorkoutDay)).all()

        groups = defaultdict(list)
        for old, new in renames.items():
            groups[new].append(old)

        for problem in problems:
            print(f'! {problem}')
        if not changes:
            print('All movement names are already in the standard form.')
            return 1 if problems else 0

        print('Renames:' if not args.apply else 'Renaming:')
        for old, new in sorted(changes.items(), key=lambda item: item[1].casefold()):
            merged = len(groups[new]) > 1
            print(f'  {old!r:40} -> {new!r}  ({exercise_counts[old]} logged){"  [merged]" if merged else ""}')
        conflicts = []
        for new, olds in groups.items():
            flags = {dip_belt[old] for old in olds}
            if len(flags) > 1:
                keep = dip_belt[new] if new in dip_belt else dip_belt[max(olds, key=lambda old: exercise_counts[old])]
                conflicts.append((new, olds, keep))
                print(f'! {new!r}: merged movements disagree on "dip belt"; keeping dip belt = {keep}')

        if not args.apply:
            print('\nNothing was changed. Run again with --apply to rename.')
            return 0

        backup = f'{database.db_file_name}.{datetime.now():%Y%m%d%H%M%S}.bak'
        shutil.copy2(database.db_file_name, backup)
        print(f'Backup saved to {backup}')

        conflict_flags = {new: keep for new, _, keep in conflicts}
        for new, olds in groups.items():
            if all(old == new for old in olds):
                continue
            if new not in dip_belt:
                session.add(Movement(name=new, dip_belt=conflict_flags.get(new, dip_belt[olds[0]])))
            elif new in conflict_flags:
                session.get(Movement, new).dip_belt = conflict_flags[new]
        session.flush()

        for exercise in session.exec(select(Exercise)).all():
            if exercise.exercise_name in changes:
                exercise.exercise_name = changes[exercise.exercise_name]
                session.add(exercise)
        for day in days:
            slots = []
            for slot in day.slots:
                movements_in_slot = list(dict.fromkeys(renames.get(name, name) for name in slot['movements']))
                slots.append({**slot, 'movements': movements_in_slot})
            day.slots = slots  # Reassign so the JSON column is saved
            session.add(day)
        for old in changes:
            if old not in groups:  # Never delete a name that something else was renamed to
                session.delete(session.get(Movement, old))
        session.commit()
        print(f'Renamed {len(changes)} movement(s).')
    return 0


if __name__ == '__main__':
    sys.exit(main())
