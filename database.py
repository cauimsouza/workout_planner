from models import Movement, User

from sqlmodel import Session, SQLModel, create_engine, select

db_file_name = '/app/data/database.db'
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

def create_db_and_tables():
    SQLModel.metadata.create_all(engine)
    seed_db()
