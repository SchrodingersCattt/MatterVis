"""Authentication contracts for hosted and private MatterVis deployments."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from typing import Any, Callable

from flask import Request

from .repository import SaaSRepository


class AuthError(RuntimeError):
    status_code = 401

    def __init__(self, message: str, *, status_code: int = 401):
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class AuthContext:
    subject: str
    workspace_id: str | None = None
    project_id: str | None = None
    scopes: frozenset[str] = field(default_factory=frozenset)
    auth_type: str = "oidc"
    key_id: str | None = None

    def has_scope(self, scope: str) -> bool:
        return "*" in self.scopes or scope in self.scopes

    @property
    def is_api_key(self) -> bool:
        return self.auth_type == "api_key"


class ApiKeyManager:
    """Project-scoped key operations with plaintext shown only once."""

    def __init__(self, repository: SaaSRepository):
        self.repository = repository

    def create(self, project_id: str, name: str, scopes: list[str]) -> tuple[str, dict[str, Any]]:
        allowed = {
            "structures:read", "structures:write", "scenes:read", "scenes:write",
            "jobs:read", "artifacts:read", "jobs:write", "*",
        }
        invalid = sorted(set(scopes) - allowed)
        if invalid:
            raise AuthError(f"unknown API key scopes: {invalid}", status_code=400)
        return self.repository.add_api_key(project_id=project_id, name=name, scopes=scopes)

    def revoke(self, project_id: str, key_id: str) -> None:
        self.repository.revoke_api_key(project_id, key_id)


class Authenticator:
    """Resolve project API keys and OIDC principals.

    Real deployments should put an OIDC-aware gateway or provider adapter in
    front of the service and set ``MATTERVIS_AUTH_MODE=oidc``.  The trusted
    ``X-User-Sub`` header is accepted only in explicit ``proxy`` mode, which
    makes local and private IdP integration testable without coupling the
    renderer to one identity vendor.
    """

    def __init__(self, repository: SaaSRepository, *, mode: str = "dev",
                 issuer_url: str | None = None, audience: str | None = None,
                 oidc_validator: Callable[[Request], dict[str, Any]] | None = None):
        self.repository = repository
        self.mode = mode
        self.issuer_url = issuer_url.rstrip("/") if issuer_url else None
        self.audience = audience
        self.oidc_validator = oidc_validator

    def _validate_bearer(self, request: Request) -> dict[str, Any]:
        authorization = request.headers.get("Authorization", "")
        if not authorization.lower().startswith("bearer "):
            raise AuthError("Bearer OIDC access token is required")
        token = authorization.split(" ", 1)[1].strip()
        try:
            import jwt

            secret = os.environ.get("MATTERVIS_OIDC_HS256_SECRET")
            if secret:
                return jwt.decode(
                    token, secret, algorithms=["HS256"], audience=self.audience,
                    issuer=self.issuer_url,
                )
            if not self.issuer_url:
                raise AuthError("MATTERVIS_OIDC_ISSUER_URL is required")
            jwks_url = os.environ.get(
                "MATTERVIS_OIDC_JWKS_URL",
                f"{self.issuer_url}/.well-known/jwks.json",
            )
            signing_key = jwt.PyJWKClient(jwks_url).get_signing_key_from_jwt(token)
            return jwt.decode(
                token, signing_key.key, algorithms=["RS256", "RS384", "RS512", "ES256", "ES384", "ES512"],
                audience=self.audience, issuer=self.issuer_url,
            )
        except AuthError:
            raise
        except Exception as exc:
            raise AuthError(f"invalid OIDC access token: {type(exc).__name__}") from exc

    def authenticate(self, request: Request) -> AuthContext:
        raw_key = request.headers.get("X-API-Key")
        if raw_key:
            record = self.repository.authenticate_api_key(raw_key)
            if record is None:
                raise AuthError("invalid or revoked API key")
            return AuthContext(
                subject=str(record["subject"]), project_id=str(record["project_id"]),
                scopes=frozenset(str(item) for item in record.get("scopes", [])),
                auth_type="api_key", key_id=str(record.get("key_id")),
            )

        if self.oidc_validator is not None:
            claims = self.oidc_validator(request)
            subject = str(claims.get("sub") or "").strip()
            if not subject:
                raise AuthError("OIDC token has no subject")
            identity = self.repository.ensure_principal(subject, claims.get("name"))
            return AuthContext(subject=subject, workspace_id=identity["workspace"]["id"])

        if self.mode == "oidc":
            claims = self._validate_bearer(request)
            subject = str(claims.get("sub") or "").strip()
            if not subject:
                raise AuthError("OIDC token has no subject")
            identity = self.repository.ensure_principal(subject, claims.get("name") or claims.get("email"))
            return AuthContext(subject=subject, workspace_id=identity["workspace"]["id"])

        if self.mode == "proxy":
            subject = (request.headers.get("X-User-Sub") or "").strip()
            if not subject:
                raise AuthError("trusted proxy identity is required")
            identity = self.repository.ensure_principal(subject, request.headers.get("X-User-Name"))
            return AuthContext(subject=subject, workspace_id=identity["workspace"]["id"])

        # Development mode is deliberately explicit and never selected by an
        # external deployment template. It keeps local API smoke tests useful.
        subject = (request.headers.get("X-User-Sub") or os.environ.get("MATTERVIS_DEV_SUBJECT") or "local").strip()
        identity = self.repository.ensure_principal(subject, request.headers.get("X-User-Name"))
        return AuthContext(subject=subject, workspace_id=identity["workspace"]["id"], auth_type="dev")
