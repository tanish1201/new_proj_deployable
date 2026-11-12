# LearnHub: Microservices on Kubernetes & Azure

*Build plan: online learning platform · 5 services + API gateway · PostgreSQL + MongoDB · AKS behind an Azure Load Balancer*

## 1. The idea

**LearnHub** is an online learning platform. Instructors publish courses, students register, enrol, work through lessons, and get notified along the way.

It suits microservices well: the domains (identity, catalog, enrolment, progress, notifications) are naturally separate, they need different data shapes (relational vs. document), and they talk to each other both synchronously (REST) and asynchronously (events).

Stretch goals for later: a React front end, a quiz/certificate service, video files in Azure Blob Storage.

## 2. Architecture

```
Internet
   │
Azure Load Balancer (public IP)   ← Kubernetes Service type=LoadBalancer
   │
gateway (NGINX) ── path routing: /api/users /api/courses /api/enrollments /api/progress
   │
   ├──► user-service ........... PostgreSQL (db: users)
   ├──► course-service ......... MongoDB    (db: courses)
   ├──► enrollment-service ..... PostgreSQL (db: enrollments) ──REST──► course-service
   └──► progress-service ....... MongoDB    (db: progress)

RabbitMQ (topic exchange "learnhub.events")
   user-service ───────user.registered────────┐
   enrollment-service ─enrollment.created─────┼──► notification-service ── PostgreSQL (db: notifications)
   progress-service ───course.completed───────┘
   enrollment-service ─enrollment.created─────────► progress-service (creates progress record)
```

**Design rules**

- **Database per service.** No service reads another service's database; they use its API or its events.
- **REST for questions, events for side-effects.** "Does this course exist?" is REST. "A student enrolled" is an event.
- **Stateless pods.** All config comes from environment variables, ConfigMaps and Secrets.
- **JWT everywhere.** user-service issues tokens; every other service validates them itself.
- **Health endpoints** (`/healthz`) on every service for Kubernetes probes and the Azure LB.

## 3. The services

| Service | Responsibility | Datastore | Key endpoints |
| --- | --- | --- | --- |
| **gateway** | Single public entry point, path routing | none | `/api/*` → services, `/healthz` |
| **user-service** | Register, login, roles (student / instructor / admin), JWT | **PostgreSQL** | `POST /users/register`, `POST /users/login`, `GET /users/me` |
| **course-service** | Course catalog: modules, lessons, search | **MongoDB** | `GET /courses`, `GET /courses/{id}`, `POST/PUT/DELETE /courses` (instructor) |
| **enrollment-service** | Enrol / unenrol, list my enrolments | **PostgreSQL** | `POST /enrollments`, `GET /enrollments/me`, `DELETE /enrollments/{id}` |
| **progress-service** | Lesson completion, quiz attempts, % complete | **MongoDB** | `POST /progress/{course}/lessons/{lesson}/complete`, `GET /progress/{course}` |
| **notification-service** | Consumes events, "sends" emails, keeps a log | **PostgreSQL** | internal only (optional `GET /notifications/me`) |

That is 5 business microservices plus a gateway, using PostgreSQL in three services and MongoDB in two.

## 4. Tech stack

| Layer | Choice |
| --- | --- |
| Language / framework | Python 3.12 + FastAPI (any language works; the architecture doesn't depend on it) |
| SQL access | SQLAlchemy + Alembic migrations, `psycopg2-binary` |
| MongoDB access | PyMongo's async client (`pymongo>=4.13`). Motor is deprecated, so avoid it for new projects |
| Messaging | RabbitMQ with `aio-pika` |
| Gateway | NGINX |
| Containers / registry | Docker, Azure Container Registry (ACR) |
| Orchestration | Kubernetes: kind or Docker Desktop locally, AKS on Azure |
| CI/CD | GitHub Actions |
| Observability | Azure Monitor Container Insights (+ optional Prometheus/Grafana) |

## 5. Repo layout

```
learnhub/
├── services/
│   ├── gateway/              # nginx.conf, Dockerfile
│   ├── user-service/         # app/, tests/, requirements.txt, Dockerfile
│   ├── course-service/
│   ├── enrollment-service/
│   ├── progress-service/
│   └── notification-service/
├── infra/
│   ├── init-db.sql           # creates the Postgres databases for local/dev
│   └── k8s/                  # namespace, deployments, services, data stores, rabbitmq
├── docker-compose.yml
├── .github/workflows/deploy.yml
└── docs/                     # API contracts + event schemas
```

## 6. Data design

**PostgreSQL**

- `users(id, email UNIQUE, password_hash, role, created_at)`
- `enrollments(id, user_id, course_id, status, enrolled_at, UNIQUE(user_id, course_id))`. `course_id` is the Mongo ObjectId as text; there is no cross-database foreign key.
- `notification_log(id, user_id, type, channel, status, payload JSONB, created_at)`

**MongoDB**

- `courses`: `{ _id, title, description, instructor_id, category, tags[], published, modules: [ { title, lessons: [ { id, title, type, content_url, duration_min } ] } ], created_at }`. Nested modules and lessons are why a document store fits here.
- `progress`: `{ _id, user_id, course_id, completed_lessons: [lesson_id], quiz_attempts: [ { quiz_id, score, taken_at } ], percent, updated_at }`, with a unique index on `(user_id, course_id)`.

## 7. Events (RabbitMQ topic exchange `learnhub.events`)

| Routing key | Published by | Consumed by | Payload |
| --- | --- | --- | --- |
| `user.registered` | user-service | notification-service | `user_id, email` |
| `enrollment.created` | enrollment-service | notification-service, progress-service | `user_id, course_id` |
| `course.completed` | progress-service | notification-service | `user_id, course_id` |

Consumers must be **idempotent**, because a message can be delivered more than once.

## 8. Build plan

### Phase 0: Setup (day 1)

- Install Docker Desktop, Python 3.12, Git, `kubectl`, Azure CLI (`az`), VS Code.
- Create a free Azure account (new accounts get trial credit) and a GitHub repo.
- Create the repo layout above.

### Phase 1: Contracts first (day 1–2)

- Write each service's endpoints (request/response JSON) and the event payloads in `docs/`. FastAPI will also generate OpenAPI docs at `/docs` once you build.
- Fix the port convention: every service listens on **8000** inside its container.

### Phase 2: user-service (PostgreSQL)

`requirements.txt`: `fastapi`, `uvicorn[standard]`, `sqlalchemy`, `psycopg2-binary`, `passlib[bcrypt]`, `bcrypt==4.0.1`, `PyJWT`, `email-validator`, `alembic`.

`app/main.py`:

```python
import os, datetime as dt
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy import create_engine, Column, Integer, String, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from passlib.context import CryptContext
import jwt

engine = create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False)
Base = declarative_base()
pwd = CryptContext(schemes=["bcrypt"])
JWT_SECRET = os.environ["JWT_SECRET"]

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(20), default="student")
    created_at = Column(DateTime, default=dt.datetime.utcnow)

@asynccontextmanager
async def lifespan(app):
    Base.metadata.create_all(engine)   # swap for Alembic migrations later
    yield

app = FastAPI(title="user-service", lifespan=lifespan)

class Creds(BaseModel):
    email: EmailStr
    password: str

def db():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()

@app.post("/users/register", status_code=201)
def register(c: Creds, s: Session = Depends(db)):
    if s.query(User).filter_by(email=c.email).first():
        raise HTTPException(409, "Email already registered")
    u = User(email=c.email, password_hash=pwd.hash(c.password))
    s.add(u); s.commit()
    return {"id": u.id, "email": u.email}

@app.post("/users/login")
def login(c: Creds, s: Session = Depends(db)):
    u = s.query(User).filter_by(email=c.email).first()
    if not u or not pwd.verify(c.password, u.password_hash):
        raise HTTPException(401, "Invalid credentials")
    token = jwt.encode(
        {"sub": str(u.id), "role": u.role,
         "exp": dt.datetime.utcnow() + dt.timedelta(hours=1)},
        JWT_SECRET, algorithm="HS256")
    return {"access_token": token, "token_type": "bearer"}

@app.get("/healthz")
def health():
    return {"status": "ok"}
```

After registering, publish `user.registered` (helper in Phase 4).

`Dockerfile` (the same pattern is used for every service):

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
RUN useradd -u 1001 appuser
USER 1001
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### Phase 3: course-service (MongoDB)

Add `pymongo>=4.13` and `PyJWT` to requirements. Copy this `auth.py` into every service that needs login:

```python
# app/auth.py
import os, jwt
from fastapi import Header, HTTPException

def current_user(authorization: str = Header(...)):
    try:
        return jwt.decode(authorization.removeprefix("Bearer "),
                          os.environ["JWT_SECRET"], algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(401, "Invalid token")
```

```python
# app/main.py (course-service)
import os, re
from bson import ObjectId
from fastapi import FastAPI, Depends, HTTPException
from pydantic import BaseModel
from pymongo import AsyncMongoClient
from .auth import current_user

db = AsyncMongoClient(os.environ["MONGO_URL"])[os.environ.get("MONGO_DB", "courses")]
app = FastAPI(title="course-service")

class CourseIn(BaseModel):
    title: str
    description: str = ""
    category: str = "general"
    modules: list[dict] = []

def out(d):
    return {**d, "_id": str(d["_id"])}

@app.get("/courses")
async def list_courses(q: str | None = None, category: str | None = None):
    flt = {"published": True}
    if category: flt["category"] = category
    if q: flt["title"] = {"$regex": re.escape(q), "$options": "i"}
    return [out(d) for d in await db.courses.find(flt).limit(50).to_list()]

@app.get("/courses/{course_id}")
async def get_course(course_id: str):
    d = await db.courses.find_one({"_id": ObjectId(course_id)})
    if not d: raise HTTPException(404, "Course not found")
    return out(d)

@app.post("/courses", status_code=201)
async def create(c: CourseIn, user=Depends(current_user)):
    if user["role"] not in ("instructor", "admin"):
        raise HTTPException(403, "Instructors only")
    doc = {**c.model_dump(), "instructor_id": user["sub"], "published": True}
    r = await db.courses.insert_one(doc)
    return {"id": str(r.inserted_id)}

@app.get("/healthz")
async def health():
    return {"status": "ok"}
```

Add a text index on `title`, `description` and `tags` once search matters.

### Phase 4: enrollment-service + the event helper (PostgreSQL + RabbitMQ)

Add `httpx`, `aio-pika`. Shared helper (copy into services that publish):

```python
# app/events.py
import json, os, aio_pika

async def publish(routing_key: str, payload: dict):
    conn = await aio_pika.connect_robust(os.environ["RABBITMQ_URL"])
    async with conn:
        ch = await conn.channel()
        ex = await ch.declare_exchange("learnhub.events",
                                       aio_pika.ExchangeType.TOPIC, durable=True)
        await ex.publish(
            aio_pika.Message(json.dumps(payload).encode(),
                             delivery_mode=aio_pika.DeliveryMode.PERSISTENT),
            routing_key=routing_key)
```

(One connection per call is fine for learning; reuse a single connection created at startup later.)

Enrolment flow:

```python
@app.post("/enrollments", status_code=201)
async def enroll(body: EnrollIn, user=Depends(current_user), s: Session = Depends(db)):
    async with httpx.AsyncClient(timeout=3) as http:
        r = await http.get(f"{os.environ['COURSE_SERVICE_URL']}/courses/{body.course_id}")
    if r.status_code == 404:
        raise HTTPException(404, "Course not found")
    r.raise_for_status()
    e = Enrollment(user_id=int(user["sub"]), course_id=body.course_id)
    s.add(e)
    try:
        s.commit()
    except IntegrityError:
        s.rollback()
        raise HTTPException(409, "Already enrolled")
    await publish("enrollment.created", {"user_id": e.user_id, "course_id": e.course_id})
    return {"id": e.id}
```

`COURSE_SERVICE_URL` is `http://course-service:8000`, which works through Kubernetes DNS and Docker Compose service names alike.

### Phase 5: progress-service and notification-service

**progress-service (MongoDB):**

- Consume `enrollment.created` and upsert an empty progress document.
- `POST /progress/{course}/lessons/{lesson}/complete` does `$addToSet` on `completed_lessons`, recalculates `percent` from the lesson count (fetched from course-service), and publishes `course.completed` when it reaches 100%.

**notification-service (PostgreSQL):** run a consumer in the app's lifespan:

```python
async def consume():
    conn = await aio_pika.connect_robust(os.environ["RABBITMQ_URL"])
    ch = await conn.channel()
    ex = await ch.declare_exchange("learnhub.events", aio_pika.ExchangeType.TOPIC, durable=True)
    q = await ch.declare_queue("notification-service", durable=True)
    for key in ("user.registered", "enrollment.created", "course.completed"):
        await q.bind(ex, routing_key=key)
    async with q.iterator() as it:
        async for msg in it:
            async with msg.process():          # ack on success, requeue on exception
                event = json.loads(msg.body)
                # 1. write a notification_log row (skip if already logged = idempotent)
                # 2. "send" the email (print/log it, or call SendGrid / Azure Communication Services)
```

Start it with `asyncio.create_task(consume())` inside the `lifespan` function. progress-service uses the same pattern for its own queue.

### Phase 6: gateway + run everything locally

`services/gateway/nginx.conf`:

```nginx
events {}
http {
  server {
    listen 80;
    location = /healthz { return 200 'ok'; }
    location /api/users/       { proxy_pass http://user-service:8000/users/; }
    location /api/courses      { proxy_pass http://course-service:8000/courses; }
    location /api/enrollments  { proxy_pass http://enrollment-service:8000/enrollments; }
    location /api/progress/    { proxy_pass http://progress-service:8000/progress/; }
  }
}
```

`services/gateway/Dockerfile`:

```dockerfile
FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/nginx.conf
```

`infra/init-db.sql`:

```sql
CREATE DATABASE users;
CREATE DATABASE enrollments;
CREATE DATABASE notifications;
```

`docker-compose.yml` (repeat the service pattern for the rest):

```yaml
services:
  postgres:
    image: postgres:16
    environment: { POSTGRES_USER: learnhub, POSTGRES_PASSWORD: "${POSTGRES_PASSWORD:?Set a local password}" }
    volumes: ["./infra/init-db.sql:/docker-entrypoint-initdb.d/init.sql", "pgdata:/var/lib/postgresql/data"]
  mongo:
    image: mongo:7
    volumes: ["mongodata:/data/db"]
  rabbitmq:
    image: rabbitmq:3-management
    ports: ["15672:15672"]
  user-service:
    build: ./services/user-service
    environment:
      DATABASE_URL: "postgresql+psycopg2://learnhub:${POSTGRES_PASSWORD}@postgres:5432/users"
      JWT_SECRET: ${JWT_SECRET:?Set a random secret of at least 32 characters}
      RABBITMQ_URL: amqp://guest:guest@rabbitmq/
    depends_on: [postgres, rabbitmq]
  course-service:
    build: ./services/course-service
    environment: { MONGO_URL: "mongodb://mongo:27017", MONGO_DB: courses, JWT_SECRET: "${JWT_SECRET:?Set a random secret of at least 32 characters}" }
    depends_on: [mongo]
  # enrollment-service, progress-service, notification-service: same pattern
  gateway:
    build: ./services/gateway
    ports: ["8080:80"]
    depends_on: [user-service, course-service]
volumes: { pgdata: {}, mongodata: {} }
```

Run `docker compose up --build`, then test:

```bash
curl -X POST localhost:8080/api/users/register -H 'Content-Type: application/json' \
     -d '{"email":"a@b.com","password":"pass1234"}'
curl localhost:8080/api/courses
```

DB-not-ready crashes on startup are common. Add `restart: on-failure` or a retry loop on first connection.

### Phase 7: Tests

- **Unit/API tests** per service: `pytest` + FastAPI `TestClient`.
- **Integration tests**: `testcontainers` for Postgres/Mongo/RabbitMQ.
- **End-to-end smoke script**: register → login → create course → enrol → complete lessons → check the notification log.
- **Load test** (later): k6 against the gateway.

## 9. Kubernetes manifests

Put these in `infra/k8s/`. Image names use a `__ACR__` placeholder that you replace at deploy time.

**Namespace and secrets** (never commit real secrets):

```bash
kubectl create namespace learnhub
kubectl create secret generic app-secrets -n learnhub \
  --from-literal=PG_PASSWORD='<choose>' \
  --from-literal=JWT_SECRET="$(openssl rand -hex 32)" \
  --from-literal=USERS_DATABASE_URL='postgresql+psycopg2://learnhub:<choose>@postgres:5432/users'
  # + ENROLLMENTS_DATABASE_URL, NOTIFICATIONS_DATABASE_URL, RABBITMQ_URL, MONGO_URL
```

**A typical service (user-service):**

```yaml
apiVersion: apps/v1
kind: Deployment
metadata: { name: user-service, namespace: learnhub }
spec:
  replicas: 2
  selector: { matchLabels: { app: user-service } }
  template:
    metadata: { labels: { app: user-service } }
    spec:
      containers:
        - name: user-service            # keep container name = service name (CI uses it)
          image: __ACR__.azurecr.io/user-service:v1
          ports: [{ containerPort: 8000 }]
          env:
            - name: JWT_SECRET
              valueFrom: { secretKeyRef: { name: app-secrets, key: JWT_SECRET } }
            - name: DATABASE_URL
              valueFrom: { secretKeyRef: { name: app-secrets, key: USERS_DATABASE_URL } }
          readinessProbe: { httpGet: { path: /healthz, port: 8000 }, initialDelaySeconds: 5 }
          livenessProbe:  { httpGet: { path: /healthz, port: 8000 }, initialDelaySeconds: 15 }
          resources:
            requests: { cpu: 100m, memory: 128Mi }
            limits:   { cpu: 500m, memory: 256Mi }
---
apiVersion: v1
kind: Service
metadata: { name: user-service, namespace: learnhub }
spec:
  selector: { app: user-service }
  ports: [{ port: 8000, targetPort: 8000 }]     # ClusterIP: internal only
```

**The one public piece, the gateway, as a LoadBalancer Service:**

```yaml
apiVersion: v1
kind: Service
metadata:
  name: gateway
  namespace: learnhub
  annotations:
    service.beta.kubernetes.io/azure-load-balancer-health-probe-request-path: /healthz
spec:
  type: LoadBalancer
  selector: { app: gateway }
  ports: [{ port: 80, targetPort: 80 }]
```

On AKS, this single object makes Azure create a Standard Load Balancer and a public IP automatically. The `gateway` Deployment is written like the user-service one, on port 80.

**Data stores in the cluster (dev/learning):**

- `postgres`: a StatefulSet (`postgres:16`, 5Gi `volumeClaimTemplate`, `PGDATA=/var/lib/postgresql/data/pgdata`) plus a ClusterIP Service named `postgres`. Mount `init-db.sql` from a ConfigMap into `/docker-entrypoint-initdb.d`.
- `mongo`: the same pattern with `mongo:7`, port 27017, data at `/data/db`.
- `rabbitmq`: a single-replica Deployment (`rabbitmq:3-management`) plus a Service on 5672.

**Production:** swap these for managed services by changing only the connection strings in the Secret: *Azure Database for PostgreSQL – Flexible Server*, *Azure Cosmos DB for MongoDB* (or MongoDB Atlas on Azure), and optionally *Azure Service Bus* instead of RabbitMQ.

**Autoscaling:** AKS ships with metrics-server, so this works out of the box:

```bash
kubectl autoscale deployment course-service -n learnhub --cpu-percent=70 --min=2 --max=5
```

## 10. Deploying to Azure (AKS + Load Balancer)

```bash
# 0. Login and variables
az login
RG=learnhub-rg; LOC=centralindia; ACR=learnhubacr$RANDOM; AKS=learnhub-aks

# 1. Resource group and container registry
az group create -n $RG -l $LOC
az acr create -g $RG -n $ACR --sku Basic

# 2. AKS cluster that is allowed to pull from your ACR
az aks create -g $RG -n $AKS --node-count 2 --node-vm-size Standard_B2ms \
  --attach-acr $ACR --generate-ssh-keys
az aks get-credentials -g $RG -n $AKS
kubectl get nodes

# 3. Build the images in Azure (no local push needed)
for s in user-service course-service enrollment-service progress-service notification-service gateway; do
  az acr build -r $ACR -t $s:v1 services/$s
done

# 4. Deploy (data stores first, gateway last)
sed -i "s/__ACR__/$ACR/g" infra/k8s/*.yaml
kubectl apply -f infra/k8s/postgres.yaml -f infra/k8s/mongo.yaml -f infra/k8s/rabbitmq.yaml
kubectl apply -f infra/k8s/user-service.yaml -f infra/k8s/course-service.yaml \
              -f infra/k8s/enrollment-service.yaml -f infra/k8s/progress-service.yaml \
              -f infra/k8s/notification-service.yaml
kubectl apply -f infra/k8s/gateway.yaml
kubectl get pods -n learnhub

# 5. Wait for the Azure Load Balancer IP
kubectl get svc gateway -n learnhub -w      # EXTERNAL-IP changes from <pending> to an address
curl http://<EXTERNAL-IP>/api/courses
```

Apply the Services before the gateway, because NGINX resolves upstream hostnames at startup.

**Later, for HTTPS and a domain name:** point a DNS record at the LB IP and add TLS. The community *ingress-nginx* project was announced for retirement in 2026, which is why this plan uses a plain NGINX gateway behind a LoadBalancer Service. When you add TLS, check Microsoft's current guidance (AKS application routing add-on or Application Gateway for Containers) rather than assuming an older tutorial still applies.

**Cost control:** the AKS control plane is free-tier, but you pay for the VMs. Run `az aks stop -g $RG -n $AKS` when idle, or `az group delete -n $RG` to remove everything.

## 11. CI/CD with GitHub Actions

Set up an Azure app registration with a federated credential for your repo (OIDC), then store `AZURE_CLIENT_ID`, `AZURE_TENANT_ID` and `AZURE_SUBSCRIPTION_ID` as repo secrets. Give the identity *AcrPush* on the registry and *Azure Kubernetes Service Cluster User* plus namespace rights on the cluster.

```yaml
name: build-and-deploy
on: { push: { branches: [main] } }
permissions: { id-token: write, contents: read }
env: { ACR: <your-acr-name>, RG: learnhub-rg, AKS: learnhub-aks }
jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: azure/login@v2
        with:
          client-id: ${{ secrets.AZURE_CLIENT_ID }}
          tenant-id: ${{ secrets.AZURE_TENANT_ID }}
          subscription-id: ${{ secrets.AZURE_SUBSCRIPTION_ID }}
      - name: Build images
        run: |
          for s in user-service course-service enrollment-service progress-service notification-service gateway; do
            az acr build -r $ACR -t $s:${{ github.sha }} services/$s
          done
      - uses: azure/aks-set-context@v4
        with: { resource-group: "${{ env.RG }}", cluster-name: "${{ env.AKS }}" }
      - name: Roll out
        run: |
          for s in user-service course-service enrollment-service progress-service notification-service gateway; do
            kubectl set image deployment/$s $s=$ACR.azurecr.io/$s:${{ github.sha }} -n learnhub
          done
```

Add a `test` job before `deploy` that runs `pytest` for each service.

## 12. Hardening checklist (after it works)

- **Secrets:** move from Kubernetes Secrets to Azure Key Vault via the CSI driver.
- **Network policies:** only the gateway may reach services; only services may reach databases.
- **Observability:** JSON logs, a `X-Request-ID` set at the gateway and passed along, `az aks enable-addons -a monitoring` for Container Insights, optional `prometheus-fastapi-instrumentator`.
- **Reliability:** timeouts and retries on service-to-service calls, RabbitMQ dead-letter queues, and the **outbox pattern** so "saved to DB" and "event published" can't diverge.
- **Performance:** the enrol example calls blocking SQLAlchemy from an async handler. Use sync `def` handlers or an async driver once load matters.
- **Security:** rate limiting at the gateway, non-root containers (already done), image scanning in CI, JWT secret rotation, and a switch to RS256 so services only need the public key.
- **Rollouts:** `maxUnavailable: 0` rolling updates, PodDisruptionBudgets, and a `/readyz` endpoint that also checks the database.

## 13. Common problems

| Symptom | Likely cause / fix |
| --- | --- |
| `ImagePullBackOff` | ACR not attached to AKS, or wrong image name/tag. Re-run `az aks update --attach-acr` |
| `CrashLoopBackOff` | Check `kubectl logs <pod> -n learnhub`. Usually a missing env var or the DB not ready |
| LB stuck on `<pending>` | `kubectl describe svc gateway -n learnhub` and check quota or region |
| Gateway 502 | Upstream Service name or port is wrong, or the pods aren't Ready |
| LB health probe failing | The `/healthz` annotation is missing or NGINX isn't returning 200 |

## 14. Suggested timeline (part-time, about 5 weeks)

| Week | Goal |
| --- | --- |
| 1 | Setup, contracts, user-service, course-service (running in Docker) |
| 2 | enrollment-service, RabbitMQ events, progress-service, notification-service |
| 3 | Gateway, docker-compose end to end, tests |
| 4 | Kubernetes manifests, ACR + AKS deployment, public Load Balancer URL |
| 5 | CI/CD, autoscaling, monitoring, hardening, README + architecture diagram for your portfolio |

## 15. Done-when checklist

- [ ] `docker compose up` runs all 5 services + gateway + databases + RabbitMQ
- [ ] Register → login → create course → enrol → complete lessons works through the gateway
- [ ] Notification log shows welcome, enrolment and completion entries
- [ ] At least 3 services use PostgreSQL and 2 use MongoDB, each with its own database
- [ ] All images are in ACR and pods are Running in AKS
- [ ] `curl http://<EXTERNAL-IP>/api/courses` works from the internet
- [ ] A push to `main` redeploys automatically
