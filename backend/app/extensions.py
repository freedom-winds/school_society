from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, scoped_session, sessionmaker


class Base(DeclarativeBase):
    pass


class Database:
    def __init__(self):
        self.engine = None
        self.Session = None

    def init_app(self, app):
        self.engine = create_engine(
            app.config["DATABASE_URL"],
            future=True,
            connect_args={"check_same_thread": False} if app.config["DATABASE_URL"].startswith("sqlite") else {},
        )
        self.Session = scoped_session(sessionmaker(bind=self.engine, autoflush=False, expire_on_commit=False))

        @app.teardown_appcontext
        def cleanup(_error=None):
            self.Session.remove()

    @property
    def session(self):
        return self.Session()


db = Database()
