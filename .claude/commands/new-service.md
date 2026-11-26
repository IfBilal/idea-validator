---
description: Scaffold a new module service with the standard skeleton
argument-hint: <service-name> <module-id e.g. M11>
---
Create `services/<name>/` following `docs/harness/03_ARCHITECTURE.md` §3 and `21_CODING_STANDARDS.md`:
FastAPI app factory with `/health`, `/ready` (checks real dependencies), `/metrics`; request-id + tracing middleware from
`libs/common`; pydantic-settings config (no localhost defaults); layered dirs (api/services/repositories/clients/models);
multi-stage non-root Dockerfile per Guide §4.4 (UID 10001, pinned base); `.dockerignore` from `.dockerignore.template`;
`pyproject.toml` in the uv workspace; tests (unit for health/ready, contract for its schemas); README (purpose, module id,
endpoints, config vars, owner); Kustomize Deployment+Service using the template in `16_KUBERNETES.md` §4; NetworkPolicy
ingress rule for its callers; add it to the GHA docker matrix and Makefile SERVICES. Args: $ARGUMENTS
