import os
import shutil
from datetime import datetime

from models import Movement, User

from sqlmodel import Session, SQLModel, create_engine, select

db_file_name = os.environ.get('DATABASE_PATH', '/app/data/database.db')
db_url = f'sqlite:///{db_file_name}'
engine = create_engine(db_url, echo=True, connect_args={'check_same_thread': False})

def seed_db():
    with Session(engine) as session:
        existing = session.exec(select(Movement)).first()
        if existing:
            print('Database already seeded')
            return

        movements = [
            Movement(name='Pull-up', dip_belt=True),
            Movement(name='Dip', dip_belt=True),
            Movement(name='Bench press', dip_belt=False),
            Movement(name='Squat', dip_belt=False),
        ]
        session.add_all(movements)

        session.commit()
        print(f'Added {len(movements)} movements')

def migrate_exercise_table():
    '''Bring an `exercise` table created by an older version up to date.

    Older versions had a NOT NULL `rpe` and no `session_id`. SQLite cannot drop a
    NOT NULL constraint in place, so the table is rebuilt. The database file is
    backed up first.
    '''
    with engine.connect() as conn:
        columns = {row[1]: row for row in conn.exec_driver_sql('PRAGMA table_info(exercise)')}
    rpe_is_not_null = columns['rpe'][3] == 1
    if 'session_id' in columns and not rpe_is_not_null:
        return

    backup = f'{db_file_name}.{datetime.now():%Y%m%d%H%M%S}.bak'
    shutil.copy2(db_file_name, backup)
    print(f'Migrating exercise table (backup saved to {backup})')

    exercise_table = SQLModel.metadata.tables['exercise']
    old_columns = ', '.join(name for name in exercise_table.columns.keys() if name in columns)
    with engine.begin() as conn:
        for index in exercise_table.indexes:
            conn.exec_driver_sql(f'DROP INDEX IF EXISTS {index.name}')
        conn.exec_driver_sql('ALTER TABLE exercise RENAME TO _exercise_old')
        exercise_table.create(conn)
        conn.exec_driver_sql(f'INSERT INTO exercise ({old_columns}) SELECT {old_columns} FROM _exercise_old')
        conn.exec_driver_sql('DROP TABLE _exercise_old')

def create_db_and_tables():
    SQLModel.metadata.create_all(engine)
    migrate_exercise_table()
    seed_db()
