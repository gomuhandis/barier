"""JSON auth endpoint for API clients.

Browsers use the form at GET/POST /login (admin_ui), which sets the JWT as
an HttpOnly cookie. Programmatic clients hit `/api/auth/login` and get the
token back in the response body to use as `Authorization: Bearer <token>`.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from src.config import get_settings
from src.security import TOKEN_TYPE, check_admin_credentials, create_access_token

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str
    expires_in: int


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest) -> TokenResponse:
    if not check_admin_credentials(payload.username, payload.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials"
        )
    s = get_settings()
    token = create_access_token(payload.username)
    return TokenResponse(
        access_token=token,
        token_type=TOKEN_TYPE,
        expires_in=s.jwt_expire_minutes * 60,
    )
