import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Type

import aio_pika
from sqlalchemy.orm import Session, sessionmaker

from common.events import publish


LOGGER = logging.getLogger(__name__)


def _pending_event(
    session_factory: sessionmaker[Session],
    event_model: Type[Any],
) -> tuple[str, str, dict[str, Any]] | None:
    with session_factory() as session:
        event = (
            session.query(event_model)
            .filter(event_model.published_at.is_(None))
            .order_by(event_model.created_at)
            .first()
        )
        if event is None:
            return None
        return event.id, event.routing_key, event.payload


def _mark_published(
    session_factory: sessionmaker[Session],
    event_model: Type[Any],
    event_id: str,
) -> None:
    with session_factory() as session:
        event = session.get(event_model, event_id)
        if event is not None and event.published_at is None:
            event.published_at = datetime.now(timezone.utc)
            session.commit()


async def publish_outbox(
    session_factory: sessionmaker[Session],
    event_model: Type[Any],
    stop: asyncio.Event,
) -> None:
    while not stop.is_set():
        event = await asyncio.to_thread(_pending_event, session_factory, event_model)
        if event is None:
            await asyncio.sleep(1)
            continue
        event_id, routing_key, payload = event
        try:
            await publish(routing_key, payload, event_id)
        except (aio_pika.exceptions.AMQPException, OSError, asyncio.TimeoutError) as exc:
            LOGGER.error("Outbox publish failed for %s; will retry: %s", event_id, exc)
            await asyncio.sleep(2)
            continue
        await asyncio.to_thread(_mark_published, session_factory, event_model, event_id)
