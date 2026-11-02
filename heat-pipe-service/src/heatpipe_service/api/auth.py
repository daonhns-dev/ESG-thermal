"""Shared Bearer API key for VTI-to-DaonH&S testbed calls."""

from __future__ import annotations

import hmac
from typing import Annotated, Callable

from fastapi import HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

bearer_scheme = HTTPBearer(auto_error=False, scheme_name="VtiBearerApiKey")


def require_bearer(expected_token: str | None) -> Callable[..., None]:
    def verify(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Security(bearer_scheme)],
    ) -> None:
        if not expected_token:
            raise HTTPException(
                503,
                detail={"code": "AUTH_NOT_CONFIGURED", "message": "API authentication is not configured"},
            )
        if credentials is None or not hmac.compare_digest(
            credentials.credentials.encode("utf-8"), expected_token.encode("utf-8")
        ):
            raise HTTPException(
                401,
                detail={"code": "UNAUTHORIZED", "message": "Valid Bearer token required"},
                headers={"WWW-Authenticate": "Bearer"},
            )

    return verify
