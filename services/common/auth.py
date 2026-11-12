import os
from typing import Annotated, Any

import jwt
from fastapi import Header, HTTPException


def require_jwt_secret() -> str:
    secret = os.environ["JWT_SECRET"]
    if len(secret) < 32:
        raise RuntimeError("JWT_SECRET must be at least 32 characters")
    return secret


def current_user(authorization: Annotated[str, Header()]) -> dict[str, Any]:
    secret = require_jwt_secret()
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Invalid authorization header")
    try:
        payload = jwt.decode(token, secret, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="Invalid token") from exc
    if not payload.get("sub") or not payload.get("role"):
        raise HTTPException(status_code=401, detail="Invalid token")
    return payload
