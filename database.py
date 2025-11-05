from models import Exercise

from sqlmodel import Session, SQLModel, create_engine, select

db_file_name = 'database.db'
db_url = f'sqlite:///{db_file_name}'
engine = create_engine(db_url, echo=True, connect_args={'check_same_thread': False})

def seed_db():
    with Session(engine) as session:
        existing = session.exec(select(Exercise)).first()
        if existing:
            print('Database already seeded')
            return
        
        exercises = [
            Exercise(name='Pull-ups'),
            Exercise(name='Dips'),
        ]
        session.add_all(exercises)
        session.commit()
        print(f'Added {len(exercises)} exercises')

def create_db_and_tables():
    SQLModel.metadata.create_all(engine)
    seed_db()