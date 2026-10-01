# MatterVis stable SaaS API

The stable tenant-scoped API lives under `/api`.  It keeps URLs resource
oriented and negotiates the contract with `X-API-Version: 1`; `/api/v2` remains
the local ViewerBackend compatibility surface.

## Authentication

- Hosted browser requests use the configured OIDC gateway.
- Set `MATTERVIS_AUTH_MODE=oidc` for direct bearer-token validation using
  `MATTERVIS_OIDC_ISSUER_URL` and `MATTERVIS_OIDC_AUDIENCE`; set
  `MATTERVIS_AUTH_MODE=proxy` when an ingress performs OIDC and forwards the
  verified subject in `X-User-Sub`.
- Automation requests use a project-scoped `X-API-Key`.
- API keys are created once through
  `POST /api/workspaces/{workspace_id}/projects/{project_id}/api-keys` and
  are stored as hashes. The plaintext key is returned only in that response.
- Supported scopes include `structures:read`, `structures:write`,
  `scenes:read`, `scenes:write`, `jobs:read`, `jobs:write`, and
  `artifacts:read`.
- Project scope and permissions are checked before every structure, scene, job,
  and artifact operation.

## Resource flow

```text
GET  /api/me
GET  /api/workspaces
GET  /api/workspaces/{workspace_id}/projects
POST /api/workspaces/{workspace_id}/projects/{project_id}/structures
GET  /api/jobs/{job_id}?project_id=...
POST /api/workspaces/{workspace_id}/projects/{project_id}/render-jobs
GET  /api/jobs/{job_id}/artifacts?project_id=...
GET  /api/artifacts/{artifact_id}/download
```

Uploads and compute operations return `202` with a durable `job_id`. Results
are returned as artifact metadata with short-lived download URLs. Mutating
uploads and job creation accept `Idempotency-Key`.

Scene patches accept `If-Match: "revision-N"` and return `409` when a stale
revision would overwrite another writer.

## Local and private deployment

For development, SQLAlchemy uses SQLite and the object store uses `.local/objects`.
Set `MATTERVIS_DATABASE_URL`, `MATTERVIS_OBJECT_STORE_BACKEND=s3`,
`MATTERVIS_S3_ENDPOINT_URL`, and `MATTERVIS_REDIS_URL` for PostgreSQL,
S3/MinIO, and the Redis worker. `deploy/docker-compose.saas.yml` provides the
single-tenant baseline.
