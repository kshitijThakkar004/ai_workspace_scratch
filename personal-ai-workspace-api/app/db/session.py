from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

settings=get_settings()

engine = create_engine(settings.database_url,
                     pool_pre_ping=True,)

SessionFactory=sessionmaker(bind=engine,
                            class_=Session,
                            autoflush=False,
                            expire_on_commit=False,)

def get_session()->Iterator[Session]:
    with SessionFactory() as session:
        yield session