import asyncio
import datetime as dt
import os
import uuid
from contextlib import asynccontextmanager
from typing import Annotated

import jwt
from fastapi import Depends, FastAPI, Header, HTTPException, status
from passlib.context import CryptContext
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator
from sqlalchemy import DateTime, Integer, JSON, String, create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Session, mapped_column, sessionmaker

from common.outbox import publish_outbox


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id = mapped_column(Integer, primary_key=True)
    email = mapped_column(String(255), unique=True, index=True, nullable=False)
    password_hash = mapped_column(String(255), nullable=False)
    role = mapped_column(String(20), default="student", nullable=False)
    created_at = mapped_column(DateTime(timezone=True), default=lambda: dt.datetime.now(dt.timezone.utc))


class OutboxEvent(Base):
    __tablename__ = "outbox_events"

    id = mapped_column(String(36), primary_key=True)
    routing_key = mapped_column(String(100), nullable=False)
    payload = mapped_column(JSON, nullable=False)
    created_at = mapped_column(DateTime(timezone=True), default=lambda: dt.datetime.now(dt.timezone.utc))
    published_at = mapped_column(DateTime(timezone=True), nullable=True)


class Credentials(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=72)

    @field_validator("password")
    @classmethod
    def validate_bcrypt_length(cls, password: str) -> str:
        if len(password.encode("utf-8")) > 72:
            raise ValueError("Password must be at most 72 UTF-8 bytes")
        return password


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    role: str


def create_app() -> FastAPI:
    database_url = os.environ["DATABASE_URL"]
    jwt_secret = os.environ["JWT_SECRET"]
    if len(jwt_secret) < 32:
        raise RuntimeError("JWT_SECRET must be at least 32 characters")

    engine = create_engine(database_url, pool_pre_ping=True)
    session_factory = sessionmaker(bind=engine, autoflush=False)
    password_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        Base.metadata.create_all(engine)
        stop = asyncio.Event()
        publisher = asyncio.create_task(publish_outbox(session_factory, OutboxEvent, stop))
        app.state.publisher_task = publisher
        try:
            yield
        finally:
            stop.set()
            await publisher
            engine.dispose()

    app = FastAPI(title="user-service", lifespan=lifespan)

    def get_db():
        with session_factory() as session:
            yield session

    @app.post("/users/register", status_code=status.HTTP_201_CREATED, response_model=UserResponse)
    def register(credentials: Credentials, session: Annotated[Session, Depends(get_db)]):
        user = User(
            email=str(credentials.email).lower(),
            password_hash=password_context.hash(credentials.password),
            role="student",
        )
        session.add(user)
        try:
            session.flush()
            session.add(
                OutboxEvent(
                    id=str(uuid.uuid4()),
                    routing_key="user.registered",
                    payload={"user_id": user.id, "email": user.email},
                )
            )
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise HTTPException(status_code=409, detail="Email already registered") from exc
        session.refresh(user)
        return user

    @app.post("/users/login")
    def login(credentials: Credentials, session: Annotated[Session, Depends(get_db)]):
        user = session.query(User).filter_by(email=str(credentials.email).lower()).first()
        if user is None or not password_context.verify(credentials.password, user.password_hash):
            raise HTTPException(status_code=401, detail="Invalid credentials")
        token = jwt.encode(
            {
                "sub": str(user.id),
                "role": user.role,
                "exp": dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1),
            },
            jwt_secret,
            algorithm="HS256",
        )
        return {"access_token": token, "token_type": "bearer"}

    @app.get("/users/me", response_model=UserResponse)
    def current_user(
        authorization: Annotated[str, Header()],
        session: Annotated[Session, Depends(get_db)],
    ):
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise HTTPException(status_code=401, detail="Invalid authorization header")
        try:
            payload = jwt.decode(token, jwt_secret, algorithms=["HS256"])
            user_id = int(payload["sub"])
        except (jwt.PyJWTError, KeyError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=401, detail="Invalid token") from exc
        user = session.get(User, user_id)
        if user is None:
            raise HTTPException(status_code=401, detail="Invalid token")
        return user

    @app.get("/healthz")
    def health():
        return {"status": "ok"}

    @app.get("/readyz")
    def readiness():
        if app.state.publisher_task.done():
            raise HTTPException(status_code=503, detail="Event publisher stopped")
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return {"status": "ready"}

    return app


app = create_app()
