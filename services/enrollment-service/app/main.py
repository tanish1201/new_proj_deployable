import asyncio
import datetime as dt
import os
import uuid
from contextlib import asynccontextmanager
from typing import Annotated, Any

import httpx
from fastapi import Depends, FastAPI, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import DateTime, Integer, JSON, String, UniqueConstraint, create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Session, mapped_column, sessionmaker

from common.auth import current_user, require_jwt_secret
from common.outbox import publish_outbox


class Base(DeclarativeBase):
    pass


class Enrollment(Base):
    __tablename__ = "enrollments"
    __table_args__ = (UniqueConstraint("user_id", "course_id", name="uq_enrollment_user_course"),)

    id = mapped_column(Integer, primary_key=True)
    user_id = mapped_column(Integer, nullable=False, index=True)
    course_id = mapped_column(String(24), nullable=False, index=True)
    status = mapped_column(String(20), nullable=False, default="active")
    enrolled_at = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: dt.datetime.now(dt.timezone.utc))


class OutboxEvent(Base):
    __tablename__ = "outbox_events"

    id = mapped_column(String(36), primary_key=True)
    routing_key = mapped_column(String(100), nullable=False)
    payload = mapped_column(JSON, nullable=False)
    created_at = mapped_column(DateTime(timezone=True), default=lambda: dt.datetime.now(dt.timezone.utc))
    published_at = mapped_column(DateTime(timezone=True), nullable=True)


class EnrollIn(BaseModel):
    course_id: str = Field(pattern=r"^[a-fA-F0-9]{24}$")


def create_app() -> FastAPI:
    engine = create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
    session_factory = sessionmaker(bind=engine, autoflush=False)
    course_service_url = os.environ.get("COURSE_SERVICE_URL", "http://course-service:8000").rstrip("/")
    require_jwt_secret()

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

    app = FastAPI(title="enrollment-service", lifespan=lifespan)

    def get_db():
        with session_factory() as session:
            yield session

    @app.post("/enrollments", status_code=status.HTTP_201_CREATED)
    def enroll(
        body: EnrollIn,
        user: Annotated[dict[str, Any], Depends(current_user)],
        session: Annotated[Session, Depends(get_db)],
    ):
        try:
            user_id = int(user["sub"])
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=401, detail="Invalid token") from exc
        try:
            response = httpx.get(f"{course_service_url}/courses/{body.course_id}", timeout=3)
        except httpx.RequestError as exc:
            raise HTTPException(status_code=503, detail="Course service unavailable") from exc
        if response.status_code == status.HTTP_404_NOT_FOUND:
            raise HTTPException(status_code=404, detail="Course not found")
        if response.is_error:
            raise HTTPException(status_code=503, detail="Course service request failed")

        enrollment = Enrollment(user_id=user_id, course_id=body.course_id.lower())
        session.add(enrollment)
        try:
            session.flush()
            session.add(
                OutboxEvent(
                    id=str(uuid.uuid4()),
                    routing_key="enrollment.created",
                    payload={"user_id": user_id, "course_id": enrollment.course_id},
                )
            )
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise HTTPException(status_code=409, detail="Already enrolled") from exc
        session.refresh(enrollment)
        return {"id": enrollment.id, "user_id": enrollment.user_id, "course_id": enrollment.course_id}

    @app.get("/enrollments/me")
    def my_enrollments(
        user: Annotated[dict[str, Any], Depends(current_user)],
        session: Annotated[Session, Depends(get_db)],
    ):
        try:
            user_id = int(user["sub"])
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=401, detail="Invalid token") from exc
        enrollments = (
            session.query(Enrollment)
            .filter_by(user_id=user_id, status="active")
            .order_by(Enrollment.enrolled_at.desc())
            .all()
        )
        return [
            {
                "id": item.id,
                "course_id": item.course_id,
                "status": item.status,
                "enrolled_at": item.enrolled_at,
            }
            for item in enrollments
        ]

    @app.delete("/enrollments/{enrollment_id}", status_code=status.HTTP_204_NO_CONTENT)
    def unenroll(
        enrollment_id: int,
        user: Annotated[dict[str, Any], Depends(current_user)],
        session: Annotated[Session, Depends(get_db)],
    ):
        try:
            user_id = int(user["sub"])
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=401, detail="Invalid token") from exc
        enrollment = session.query(Enrollment).filter_by(id=enrollment_id, user_id=user_id).first()
        if enrollment is None:
            raise HTTPException(status_code=404, detail="Enrollment not found")
        session.delete(enrollment)
        session.commit()
        return None

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
