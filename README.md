# LearnHub

LearnHub is an online-learning platform organized as five business services and an NGINX API gateway. Services are containerized and the Kubernetes manifests target kind/Docker Desktop for development or AKS for deployment.

## Services and routes

| Service | Responsibility | Storage / messaging | Gateway paths |
| --- | --- | --- | --- |
| `user-service` | Student registration, login, JWT, current user | PostgreSQL; publishes `user.registered` | `/api/users/*` |
| `course-service` | Published course catalog and instructor/admin course creation | MongoDB | `/api/courses*` |
| `enrollment-service` | Enrol, list own enrolments, unenrol | PostgreSQL; publishes `enrollment.created` | `/api/enrollments*` |
| `progress-service` | Lesson completion and course percentage | MongoDB; consumes enrolments and publishes completion | `/api/progress/*` |
| `notification-service` | Idempotent event log; logs the simulated email | PostgreSQL; consumes user/enrolment/completion events | `/api/notifications/*` |
| `gateway` | Public path routing and health endpoint | NGINX | `/healthz` |

RabbitMQ carries events through the durable `learnhub.events` topic exchange. Registration and enrolment events use a database outbox; consumers use durable queues and a dead-letter queue. Notification records are deduplicated by event ID. The notification service logs email activity; it does not send real email.

Public registration always assigns the `student` role. Create instructor/admin accounts through an authorized administrative process; there is intentionally no public role-elevation endpoint.

## Run locally

1. Copy `.env.example` to `.env`.
2. Replace all placeholder passwords and secrets. `JWT_SECRET` must be at least 32 characters. Keep the PostgreSQL and RabbitMQ passwords URL-safe, or URL-encode them wherever they appear in connection URLs.
3. Start the stack:

   ```powershell
   docker compose up --build
   ```

4. Register and log in through the gateway:

   ```powershell
   $user = Invoke-RestMethod -Method Post http://localhost:8080/api/users/register `
     -ContentType 'application/json' -Body '{"email":"student@example.com","password":"pass12345"}'
   $login = Invoke-RestMethod -Method Post http://localhost:8080/api/users/login `
     -ContentType 'application/json' -Body '{"email":"student@example.com","password":"pass12345"}'
   $token = $login.access_token
   ```

   Create an instructor account out-of-band before trying course creation. Then create a course with nested module/lesson IDs, enrol in its ObjectId using the student's token, mark lessons complete through `/api/progress/{course_id}/lessons/{lesson_id}/complete`, and view the notification log at `/api/notifications/me`.

The Compose stack is intended for local development. Do not reuse its credentials in a deployed environment.

Run the API tests after installing the service and test dependencies:

```powershell
python -m pip install -r services/user-service/requirements.txt `
  -r services/course-service/requirements.txt `
  -r services/enrollment-service/requirements.txt `
  -r services/progress-service/requirements.txt `
  -r services/notification-service/requirements.txt `
  -r requirements-dev.txt
python -m pytest -q
```

For local course-creation testing, promote a known account through an authorized database operation, for example:

```powershell
docker compose exec postgres psql -U learnhub -d users `
  -c "UPDATE users SET role = 'instructor' WHERE email = 'instructor@example.com'"
```

## Build images

The service Dockerfiles use the repository root as the build context so they can include `services/common`:

```powershell
docker build -f services/user-service/Dockerfile -t learnhub/user-service:dev .
docker build -f services/course-service/Dockerfile -t learnhub/course-service:dev .
docker build -f services/enrollment-service/Dockerfile -t learnhub/enrollment-service:dev .
docker build -f services/progress-service/Dockerfile -t learnhub/progress-service:dev .
docker build -f services/notification-service/Dockerfile -t learnhub/notification-service:dev .
docker build -f services/gateway/Dockerfile -t learnhub/gateway:dev services/gateway
```

For kind, load the five application images and gateway with `kind load docker-image <image>`. For AKS, push fully-qualified image names to ACR and update image references in `infra/k8s/services.yaml`.

## CI/CD

The [GitHub Actions workflow](./.github/workflows/deploy.yml) runs the API tests on pull requests and pushes to `main`. A push to `main` builds all six images in ACR and rolls them out to AKS. Configure repository variables `ACR_NAME`, `AZURE_RESOURCE_GROUP`, and `AKS_CLUSTER_NAME`, and repository secrets `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, and `AZURE_SUBSCRIPTION_ID` for an Azure federated identity. Grant that identity ACR push rights and permission to update deployments in the `learnhub` namespace. Bootstrap the cluster, namespace, and `app-secrets` before the first workflow deployment.

## Deploy to Kubernetes

The included PostgreSQL, MongoDB and RabbitMQ StatefulSets are single-replica learning/development infrastructure, not production HA. For production, use managed services, persistent/backup policies, Key Vault or another managed secret provider, and appropriate network policies.

Create the namespace and secret first. Replace all placeholders; URL-encode reserved characters in PostgreSQL and AMQP URLs. The user/password values for the stores must match their connection URLs.

```powershell
kubectl create namespace learnhub
kubectl create secret generic app-secrets -n learnhub `
  --from-literal=POSTGRES_USER='learnhub' `
  --from-literal=POSTGRES_PASSWORD='<url-safe-strong-password>' `
  --from-literal=RABBITMQ_USER='learnhub' `
  --from-literal=RABBITMQ_PASSWORD='<url-safe-strong-password>' `
  --from-literal=JWT_SECRET='<random-secret-at-least-32-characters>' `
  --from-literal=USERS_DATABASE_URL='postgresql+psycopg2://learnhub:<url-encoded-password>@postgres:5432/users' `
  --from-literal=ENROLLMENTS_DATABASE_URL='postgresql+psycopg2://learnhub:<url-encoded-password>@postgres:5432/enrollments' `
  --from-literal=NOTIFICATIONS_DATABASE_URL='postgresql+psycopg2://learnhub:<url-encoded-password>@postgres:5432/notifications' `
  --from-literal=MONGO_URL='mongodb://mongo:27017' `
  --from-literal=RABBITMQ_URL='amqp://learnhub:<url-encoded-password>@rabbitmq:5672/'
kubectl apply -f infra/k8s/namespace.yaml
kubectl apply -f infra/k8s/rabbitmq.yaml
kubectl apply -f infra/k8s/services.yaml
kubectl get pods,svc -n learnhub
```

Create `app-secrets` only after `learnhub` exists. Applying the manifests creates PostgreSQL, MongoDB and RabbitMQ before the application deployments; the gateway and its public `LoadBalancer` Service are last. Check rollout and logs with `kubectl get pods -n learnhub` and `kubectl logs deployment/<service> -n learnhub`.

When deploying to AKS, publish images to ACR first and replace the `learnhub/<service>:dev` image values with `<registry>.azurecr.io/<service>:<tag>`. The public IP is assigned to the gateway Service:

```powershell
kubectl get svc gateway -n learnhub -w
```

For the original architecture, phased roadmap and Azure notes, see [LearnHub Microservices on Kubernetes & Azure](./LearnHub%20Microservices%20on%20Kubernetes%20%26%20Azure.md).
