import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
from typing import Annotated, Any

import httpx
import aio_pika
from bson import ObjectId
from fastapi import Depends, FastAPI, HTTPException
from pymongo import AsyncMongoClient
from pymongo.errors import PyMongoError

from common.auth import current_user, require_jwt_secret
from common.events import declare_event_queue, publish


LOGGER = logging.getLogger(__name__)


def create_app() -> FastAPI:
    require_jwt_secret()
    client = AsyncMongoClient(os.environ["MONGO_URL"])
    database = client[os.environ.get("MONGO_DB", "progress")]
    course_service_url = os.environ.get("COURSE_SERVICE_URL", "http://course-service:8000").rstrip("/")

    async def consume_enrollments_once() -> None:
        connection = await aio_pika.connect_robust(os.environ["RABBITMQ_URL"], timeout=5)
        try:
            channel = await connection.channel()
            exchange, queue = await declare_event_queue(channel, "progress-service")
            await queue.bind(exchange, routing_key="enrollment.created")
            async with queue.iterator() as iterator:
                async for message in iterator:
                    try:
                        event = json.loads(message.body)
                        user_id = str(event["user_id"])
                        course_id = str(event["course_id"])
                    except (json.JSONDecodeError, KeyError, TypeError, UnicodeDecodeError) as exc:
                        LOGGER.error("Rejecting invalid enrollment event: %s", exc)
                        await message.reject(requeue=False)
                        continue
                    if not user_id.isdecimal() or int(user_id) < 1 or not ObjectId.is_valid(course_id):
                        LOGGER.error("Rejecting enrollment event with invalid user or course id")
                        await message.reject(requeue=False)
                        continue
                    course_id = course_id.lower()
                    async with message.process(requeue=True):
                        await database.progress.update_one(
                            {"user_id": user_id, "course_id": course_id},
                            {
                                "$setOnInsert": {
                                    "user_id": user_id,
                                    "course_id": course_id,
                                    "completed_lessons": [],
                                    "percent": 0.0,
                                    "updated_at": datetime.now(timezone.utc),
                                }
                            },
                            upsert=True,
                        )
        finally:
            await connection.close()

    async def consume_enrollments() -> None:
        while True:
            try:
                await consume_enrollments_once()
            except (
                aio_pika.exceptions.AMQPException,
                OSError,
                asyncio.TimeoutError,
                PyMongoError,
            ) as exc:
                LOGGER.error("Progress event consumer disconnected; retrying: %s", exc)
                await asyncio.sleep(2)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await database.progress.create_index(
            [("user_id", 1), ("course_id", 1)],
            unique=True,
            name="uq_progress_user_course",
        )
        consumer = asyncio.create_task(consume_enrollments())
        app.state.consumer_task = consumer
        try:
            yield
        finally:
            consumer.cancel()
            with suppress(asyncio.CancelledError):
                await consumer
            await client.close()

    app = FastAPI(title="progress-service", lifespan=lifespan)

    async def load_course(course_id: str) -> dict[str, Any]:
        if not ObjectId.is_valid(course_id):
            raise HTTPException(status_code=404, detail="Course not found")
        try:
            async with httpx.AsyncClient(timeout=3) as http:
                response = await http.get(f"{course_service_url}/courses/{course_id}")
        except httpx.RequestError as exc:
            raise HTTPException(status_code=503, detail="Course service unavailable") from exc
        if response.status_code == 404:
            raise HTTPException(status_code=404, detail="Course not found")
        if response.is_error:
            raise HTTPException(status_code=503, detail="Course service request failed")
        return response.json()

    @app.post("/progress/{course_id}/lessons/{lesson_id}/complete")
    async def complete_lesson(
        course_id: str,
        lesson_id: str,
        user: Annotated[dict[str, Any], Depends(current_user)],
    ):
        course = await load_course(course_id)
        lesson_ids = {
            str(lesson["id"])
            for module in course.get("modules", [])
            for lesson in module.get("lessons", [])
            if isinstance(lesson, dict) and lesson.get("id") is not None
        }
        lesson_count = len(lesson_ids)
        if lesson_count == 0:
            raise HTTPException(status_code=409, detail="Course has no lessons")
        if lesson_id not in lesson_ids:
            raise HTTPException(status_code=404, detail="Lesson not found")

        user_id = str(user["sub"])
        course_id = course_id.lower()
        await database.progress.update_one(
            {"user_id": user_id, "course_id": course_id},
            [
                {
                    "$set": {
                        "user_id": user_id,
                        "course_id": course_id,
                        "completed_lessons": {
                            "$setUnion": [
                                {"$ifNull": ["$completed_lessons", []]},
                                [lesson_id],
                            ]
                        },
                        "updated_at": datetime.now(timezone.utc),
                    }
                },
                {
                    "$set": {
                        "percent": {
                            "$round": [
                                {
                                    "$multiply": [
                                        {"$divide": [{"$size": "$completed_lessons"}, lesson_count]},
                                        100,
                                    ]
                                },
                                2,
                            ]
                        }
                    }
                },
            ],
            upsert=True,
        )
        record = await database.progress.find_one({"user_id": user_id, "course_id": course_id})
        completed = set(record.get("completed_lessons", []))
        percent = record["percent"]
        if percent >= 100:
            event_id = f"course.completed:{user_id}:{course_id}"
            try:
                await publish(
                    "course.completed",
                    {"user_id": user_id, "course_id": course_id},
                    event_id,
                )
            except (aio_pika.exceptions.AMQPException, OSError, asyncio.TimeoutError) as exc:
                LOGGER.error("Could not publish course completion event %s: %s", event_id, exc)
                raise HTTPException(status_code=503, detail="Event service unavailable") from exc
        return {
            "course_id": course_id,
            "completed_lessons": sorted(completed),
            "percent": percent,
        }

    @app.get("/progress/{course_id}")
    async def get_progress(
        course_id: str,
        user: Annotated[dict[str, Any], Depends(current_user)],
    ):
        course_id = course_id.lower()
        record = await database.progress.find_one(
            {"user_id": str(user["sub"]), "course_id": course_id}
        )
        if record is None:
            raise HTTPException(status_code=404, detail="Progress not found")
        return {
            "course_id": course_id,
            "completed_lessons": record.get("completed_lessons", []),
            "percent": record.get("percent", 0.0),
            "updated_at": record.get("updated_at"),
        }

    @app.get("/healthz")
    async def health():
        return {"status": "ok"}

    @app.get("/readyz")
    async def readiness():
        if app.state.consumer_task.done():
            raise HTTPException(status_code=503, detail="Event consumer stopped")
        await client.admin.command("ping")
        return {"status": "ready"}

    return app


app = create_app()
