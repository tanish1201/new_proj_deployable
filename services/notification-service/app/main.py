import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
from typing import Annotated, Any

import aio_pika
from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy import DateTime, Integer, String, create_engine, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Session, mapped_column, sessionmaker

from common.auth import current_user, require_jwt_secret
from common.events import declare_event_queue


LOGGER = logging.getLogger(__name__)
EVENTS = {"user.registered", "enrollment.created", "course.completed"}


class Base(DeclarativeBase):
    pass


class Notification(Base):
    __tablename__ = "notification_log"

    id = mapped_column(Integer, primary_key=True)
    event_id = mapped_column(String(200), unique=True, nullable=False)
    user_id = mapped_column(String(100), nullable=False, index=True)
    type = mapped_column(String(100), nullable=False)
    channel = mapped_column(String(30), nullable=False, default="log")
    status = mapped_column(String(30), nullable=False, default="recorded")
    payload = mapped_column(JSONB, nullable=False)
    created_at = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


def create_app() -> FastAPI:
    require_jwt_secret()
    engine = create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
    session_factory = sessionmaker(bind=engine, autoflush=False)

    def save_notification(
        event_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        with session_factory() as session:
            session.add(
                Notification(
                    event_id=event_id,
                    user_id=str(payload["user_id"]),
                    type=event_type,
                    payload=payload,
                )
            )
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
                if constraint != "notification_log_event_id_key":
                    raise

    async def consume_events_once() -> None:
        connection = await aio_pika.connect_robust(os.environ["RABBITMQ_URL"], timeout=5)
        try:
            channel = await connection.channel()
            exchange, queue = await declare_event_queue(channel, "notification-service")
            for event_type in EVENTS:
                await queue.bind(exchange, routing_key=event_type)
            async with queue.iterator() as iterator:
                async for message in iterator:
                    try:
                        event_type = message.routing_key
                        payload = json.loads(message.body)
                        event_id = message.message_id
                        if event_type not in EVENTS:
                            raise ValueError(f"Unsupported event type: {event_type}")
                        if not event_id or not isinstance(payload, dict) or "user_id" not in payload:
                            raise ValueError("Event is missing its id or user_id")
                    except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError) as exc:
                        LOGGER.error("Rejecting invalid event: %s", exc)
                        await message.reject(requeue=False)
                        continue
                    async with message.process(requeue=True):
                        await asyncio.to_thread(save_notification, event_id, event_type, payload)
                        LOGGER.info(
                            "Recorded notification and simulated email for user %s: %s",
                            payload["user_id"],
                            event_type,
                        )
        finally:
            await connection.close()

    async def consume_events() -> None:
        while True:
            try:
                await consume_events_once()
            except (
                aio_pika.exceptions.AMQPException,
                OSError,
                asyncio.TimeoutError,
                SQLAlchemyError,
            ) as exc:
                LOGGER.error("Notification event consumer disconnected; retrying: %s", exc)
                await asyncio.sleep(2)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        Base.metadata.create_all(engine)
        consumer = asyncio.create_task(consume_events())
        app.state.consumer_task = consumer
        try:
            yield
        finally:
            consumer.cancel()
            with suppress(asyncio.CancelledError):
                await consumer
            engine.dispose()

    app = FastAPI(title="notification-service", lifespan=lifespan)

    @app.get("/notifications/me")
    def my_notifications(user: Annotated[dict[str, Any], Depends(current_user)]):
        user_id = str(user["sub"])
        with session_factory() as session:
            notifications = (
                session.query(Notification)
                .filter_by(user_id=user_id)
                .order_by(Notification.created_at.desc())
                .limit(50)
                .all()
            )
            return [
                {
                    "id": item.id,
                    "type": item.type,
                    "channel": item.channel,
                    "status": item.status,
                    "payload": item.payload,
                    "created_at": item.created_at,
                }
                for item in notifications
            ]

    @app.get("/healthz")
    def health():
        return {"status": "ok"}

    @app.get("/readyz")
    def readiness():
        if app.state.consumer_task.done():
            raise HTTPException(status_code=503, detail="Event consumer stopped")
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return {"status": "ready"}

    return app


app = create_app()
