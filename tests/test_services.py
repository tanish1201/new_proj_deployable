import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import jwt
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session


ROOT = Path(__file__).resolve().parents[1]
SERVICES_DIR = ROOT / "services"
sys.path.insert(0, str(SERVICES_DIR))
JWT_SECRET = "test-secret-with-more-than-32-characters"


def load_service(service: str, monkeypatch, **environment):
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("JWT_SECRET", JWT_SECRET)
    module_path = SERVICES_DIR / service / "app" / "main.py"
    module_name = f"learnhub_{service.replace('-', '_')}_{uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_token(user_id: str = "17", role: str = "student") -> str:
    return jwt.encode(
        {
            "sub": user_id,
            "role": role,
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        },
        JWT_SECRET,
        algorithm="HS256",
    )


def test_user_register_login_and_profile_emit_outbox_event(tmp_path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'users.db'}"
    module = load_service(
        "user-service",
        monkeypatch,
        DATABASE_URL=database_url,
        RABBITMQ_URL="amqp://guest:guest@localhost:5672/",
    )
    engine = create_engine(database_url)
    module.Base.metadata.create_all(engine)

    client = TestClient(module.app)
    registered = client.post(
        "/users/register",
        json={"email": "Student@Example.com", "password": "pass12345"},
    )
    assert registered.status_code == 201
    assert registered.json()["role"] == "student"
    assert client.post(
        "/users/register",
        json={"email": "student@example.com", "password": "pass12345"},
    ).status_code == 409

    login = client.post(
        "/users/login",
        json={"email": "student@example.com", "password": "pass12345"},
    )
    assert login.status_code == 200
    profile = client.get(
        "/users/me",
        headers={"Authorization": f"Bearer {login.json()['access_token']}"},
    )
    assert profile.status_code == 200
    assert profile.json()["email"] == "student@example.com"

    with Session(engine) as session:
        event = session.query(module.OutboxEvent).one()
        assert event.routing_key == "user.registered"
        assert event.payload["email"] == "student@example.com"


def test_enrollment_flow_is_owner_scoped_and_queues_event(tmp_path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'enrollments.db'}"
    module = load_service(
        "enrollment-service",
        monkeypatch,
        DATABASE_URL=database_url,
        COURSE_SERVICE_URL="http://course-service:8000",
    )
    engine = create_engine(database_url)
    module.Base.metadata.create_all(engine)
    monkeypatch.setattr(
        module.httpx,
        "get",
        lambda *_args, **_kwargs: SimpleNamespace(status_code=200, is_error=False),
    )
    headers = {"Authorization": f"Bearer {make_token()}"}
    body = {"course_id": "507f1f77bcf86cd799439011"}

    client = TestClient(module.app)
    enrolled = client.post("/enrollments", json=body, headers=headers)
    assert enrolled.status_code == 201
    enrollment_id = enrolled.json()["id"]
    assert client.get("/enrollments/me", headers=headers).json()[0]["course_id"] == body["course_id"]
    assert client.post("/enrollments", json=body, headers=headers).status_code == 409
    assert client.delete(
        f"/enrollments/{enrollment_id}",
        headers={"Authorization": f"Bearer {make_token(user_id='18')}"},
    ).status_code == 404
    assert client.delete(f"/enrollments/{enrollment_id}", headers=headers).status_code == 204

    with Session(engine) as session:
        event = session.query(module.OutboxEvent).one()
        assert event.routing_key == "enrollment.created"
        assert event.payload == {"user_id": 17, "course_id": body["course_id"]}


def test_course_lookup_rejects_malformed_object_id(monkeypatch):
    module = load_service(
        "course-service",
        monkeypatch,
        MONGO_URL="mongodb://localhost:27017",
        MONGO_DB="courses",
    )
    response = TestClient(module.app).get("/courses/not-an-object-id")
    assert response.status_code == 404


def test_student_cannot_write_courses(monkeypatch):
    module = load_service(
        "course-service",
        monkeypatch,
        MONGO_URL="mongodb://localhost:27017",
        MONGO_DB="courses",
    )
    client = TestClient(module.app)
    headers = {"Authorization": f"Bearer {make_token()}"}
    body = {"title": "Course"}
    course_id = "507f1f77bcf86cd799439011"
    assert client.post("/courses", json=body, headers=headers).status_code == 403
    assert client.put(f"/courses/{course_id}", json=body, headers=headers).status_code == 403
    assert client.delete(f"/courses/{course_id}", headers=headers).status_code == 403


def test_progress_rejects_invalid_auth_before_storage_access(monkeypatch):
    module = load_service(
        "progress-service",
        monkeypatch,
        MONGO_URL="mongodb://localhost:27017",
        MONGO_DB="progress",
        RABBITMQ_URL="amqp://guest:guest@localhost:5672/",
    )
    response = TestClient(module.app).get(
        "/progress/507f1f77bcf86cd799439011",
        headers={"Authorization": "Bearer invalid"},
    )
    assert response.status_code == 401


def test_notifications_reject_invalid_auth_before_storage_access(monkeypatch):
    module = load_service(
        "notification-service",
        monkeypatch,
        DATABASE_URL="postgresql+psycopg2://learnhub:secret@localhost/notifications",
        RABBITMQ_URL="amqp://guest:guest@localhost:5672/",
    )
    response = TestClient(module.app).get(
        "/notifications/me",
        headers={"Authorization": "Bearer invalid"},
    )
    assert response.status_code == 401
