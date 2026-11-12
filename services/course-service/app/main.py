import os
import re
from contextlib import asynccontextmanager
from typing import Annotated, Any

from bson import ObjectId
from fastapi import Depends, FastAPI, HTTPException, Response, status
from pydantic import BaseModel, Field
from pymongo import AsyncMongoClient

from common.auth import current_user, require_jwt_secret


class CourseIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=10000)
    category: str = Field(default="general", max_length=100)
    tags: list[str] = Field(default_factory=list)
    modules: list[dict[str, Any]] = Field(default_factory=list)


def create_app() -> FastAPI:
    mongo_url = os.environ["MONGO_URL"]
    mongo_db = os.environ.get("MONGO_DB", "courses")
    require_jwt_secret()
    client = AsyncMongoClient(mongo_url)
    database = client[mongo_db]

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        await client.close()

    app = FastAPI(title="course-service", lifespan=lifespan)

    def serialize(course: dict[str, Any]) -> dict[str, Any]:
        return {**course, "_id": str(course["_id"])}

    @app.get("/courses")
    async def list_courses(q: str | None = None, category: str | None = None):
        filters: dict[str, Any] = {"published": True}
        if category:
            filters["category"] = category
        if q:
            filters["title"] = {"$regex": re.escape(q), "$options": "i"}
        courses = await database.courses.find(filters).limit(50).to_list(length=50)
        return [serialize(course) for course in courses]

    @app.get("/courses/{course_id}")
    async def get_course(course_id: str):
        if not ObjectId.is_valid(course_id):
            raise HTTPException(status_code=404, detail="Course not found")
        course = await database.courses.find_one({"_id": ObjectId(course_id), "published": True})
        if course is None:
            raise HTTPException(status_code=404, detail="Course not found")
        return serialize(course)

    @app.post("/courses", status_code=201)
    async def create_course(course: CourseIn, user: Annotated[dict, Depends(current_user)]):
        if user.get("role") not in {"instructor", "admin"}:
            raise HTTPException(status_code=403, detail="Instructors only")
        document = {
            **course.model_dump(),
            "instructor_id": user["sub"],
            "published": True,
        }
        result = await database.courses.insert_one(document)
        return {"id": str(result.inserted_id)}

    @app.put("/courses/{course_id}")
    async def update_course(
        course_id: str,
        course: CourseIn,
        user: Annotated[dict, Depends(current_user)],
    ):
        if user.get("role") not in {"instructor", "admin"}:
            raise HTTPException(status_code=403, detail="Instructors only")
        if not ObjectId.is_valid(course_id):
            raise HTTPException(status_code=404, detail="Course not found")
        filters: dict[str, Any] = {"_id": ObjectId(course_id)}
        if user["role"] != "admin":
            filters["instructor_id"] = user["sub"]
        result = await database.courses.update_one(
            filters,
            {"$set": course.model_dump()},
        )
        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="Course not found")
        return {"id": course_id.lower()}

    @app.delete("/courses/{course_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_course(course_id: str, user: Annotated[dict, Depends(current_user)]):
        if user.get("role") not in {"instructor", "admin"}:
            raise HTTPException(status_code=403, detail="Instructors only")
        if not ObjectId.is_valid(course_id):
            raise HTTPException(status_code=404, detail="Course not found")
        filters: dict[str, Any] = {"_id": ObjectId(course_id)}
        if user["role"] != "admin":
            filters["instructor_id"] = user["sub"]
        result = await database.courses.delete_one(filters)
        if result.deleted_count == 0:
            raise HTTPException(status_code=404, detail="Course not found")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get("/healthz")
    async def health():
        return {"status": "ok"}

    @app.get("/readyz")
    async def readiness():
        await client.admin.command("ping")
        return {"status": "ready"}

    return app


app = create_app()
