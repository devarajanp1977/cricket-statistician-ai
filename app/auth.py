"""Supabase JWT verification + per-user data persistence.

The cricket data lives in DuckDB on the backend VM. Supabase only stores:
  - auth.users (managed by Supabase Auth)
  - public.chat_history
  - public.bookmarks
  - public.user_preferences

This module:
  1. Verifies the JWT issued by Supabase Auth (RS256 via JWKS) on every
     authenticated request.
  2. Provides a thin REST client (using the *service-role* key) that the
     backend uses to write to the user-scoped tables. We use service-role
     because the backend is trusted; we still scope every insert/select to
     the authenticated user_id we extracted from the JWT.

Required environment variables:
  - SUPABASE_PROJECT_URL    e.g. https://ajlalmmcqbdxeemlhfro.supabase.co
  - SUPABASE_JWT_SECRET     OR  SUPABASE_JWKS_URL (optional, defaults to project)
  - SUPABASE_SERVICE_ROLE_KEY  (only needed for the data-write helpers)

If SUPABASE_PROJECT_URL is not set, authentication is *disabled* and
get_current_user() returns None. This keeps local dev frictionless.
"""

from __future__ import annotations

import os
import time
from typing import Any, Optional

import httpx
import jwt
from fastapi import Depends, Header, HTTPException, status
from jwt import PyJWKClient

SUPABASE_URL = os.getenv("SUPABASE_PROJECT_URL", "").rstrip("/")
SUPABASE_JWT_SECRET = os.getenv("SUPABASE_JWT_SECRET", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
SUPABASE_JWKS_URL = os.getenv("SUPABASE_JWKS_URL") or (
    f"{SUPABASE_URL}/auth/v1/.well-known/jwks.json" if SUPABASE_URL else ""
)

AUTH_ENABLED = bool(SUPABASE_URL and (SUPABASE_JWT_SECRET or SUPABASE_JWKS_URL))

_jwks_client: Optional[PyJWKClient] = None
if SUPABASE_JWKS_URL and not SUPABASE_JWT_SECRET:
    # PyJWKClient caches keys for an hour by default.
    _jwks_client = PyJWKClient(SUPABASE_JWKS_URL, cache_keys=True, lifespan=3600)


class AuthUser:
    """Authenticated Supabase user extracted from a verified JWT."""

    __slots__ = ("id", "email", "role", "claims")

    def __init__(self, claims: dict[str, Any]):
        self.id: str = claims["sub"]
        self.email: Optional[str] = claims.get("email")
        self.role: str = claims.get("role", "authenticated")
        self.claims = claims

    def __repr__(self) -> str:  # pragma: no cover
        return f"AuthUser(id={self.id!r}, email={self.email!r})"


def _decode(token: str) -> dict[str, Any]:
    """Decode + verify a Supabase JWT. Raises jwt exceptions on failure."""
    if SUPABASE_JWT_SECRET:
        # Legacy HS256 secret (older Supabase projects).
        return jwt.decode(
            token,
            SUPABASE_JWT_SECRET,
            algorithms=["HS256"],
            audience="authenticated",
            options={"require": ["exp", "sub"]},
        )
    if _jwks_client is None:
        raise RuntimeError("Supabase auth not configured")
    signing_key = _jwks_client.get_signing_key_from_jwt(token).key
    return jwt.decode(
        token,
        signing_key,
        algorithms=["RS256", "ES256"],
        audience="authenticated",
        options={"require": ["exp", "sub"]},
    )


async def get_current_user(
    authorization: Optional[str] = Header(default=None),
) -> Optional[AuthUser]:
    """FastAPI dependency: returns the authenticated user or None.

    Returns None when:
      - auth is not configured (local dev), or
      - no Authorization header was provided.

    Raises 401 when a header is provided but the token is invalid/expired.
    """
    if not AUTH_ENABLED:
        return None
    if not authorization:
        return None
    parts = authorization.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header must be 'Bearer <jwt>'",
        )
    token = parts[1].strip()
    try:
        claims = _decode(token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired"
        )
    except jwt.InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=f"Invalid token: {exc}"
        )
    return AuthUser(claims)


async def require_user(
    user: Optional[AuthUser] = Depends(get_current_user),
) -> AuthUser:
    """FastAPI dependency: authentication required (401 if missing)."""
    if user is None:
        if not AUTH_ENABLED:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Authentication is not configured on this server.",
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )
    return user


# ---------------------------------------------------------------------------
# Supabase REST helper for the user-scoped tables.
# ---------------------------------------------------------------------------

class SupabaseRest:
    """Tiny PostgREST client bound to the service-role key.

    Every method requires an explicit user_id and writes/filters by it,
    matching the RLS policies defined in the migration (and providing
    defence-in-depth in case service-role bypasses RLS).
    """

    def __init__(self, url: str = SUPABASE_URL, service_key: str = SUPABASE_SERVICE_KEY):
        self.url = url.rstrip("/")
        self.service_key = service_key
        self._client: Optional[httpx.AsyncClient] = None

    @property
    def configured(self) -> bool:
        return bool(self.url and self.service_key)

    def _client_get(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=f"{self.url}/rest/v1",
                headers={
                    "apikey": self.service_key,
                    "Authorization": f"Bearer {self.service_key}",
                    "Content-Type": "application/json",
                    "Prefer": "return=representation",
                },
                timeout=15.0,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def insert_chat_turn(
        self,
        user_id: str,
        session_id: str,
        role: str,
        content: str,
        metadata: Optional[dict] = None,
    ) -> dict:
        if not self.configured:
            return {}
        payload = {
            "user_id": user_id,
            "session_id": session_id,
            "role": role,
            "content": content,
            "metadata": metadata or {},
        }
        r = await self._client_get().post("/chat_history", json=payload)
        r.raise_for_status()
        data = r.json()
        return data[0] if isinstance(data, list) and data else {}

    async def list_chat_history(
        self, user_id: str, session_id: Optional[str] = None, limit: int = 100
    ) -> list[dict]:
        if not self.configured:
            return []
        params: dict[str, Any] = {
            "user_id": f"eq.{user_id}",
            "order": "created_at.asc",
            "limit": str(limit),
        }
        if session_id:
            params["session_id"] = f"eq.{session_id}"
        r = await self._client_get().get("/chat_history", params=params)
        r.raise_for_status()
        return r.json()

    async def add_bookmark(
        self,
        user_id: str,
        title: str,
        query: str,
        answer: Optional[str] = None,
        tags: Optional[list[str]] = None,
    ) -> dict:
        if not self.configured:
            return {}
        payload = {
            "user_id": user_id,
            "title": title,
            "query": query,
            "answer": answer,
            "tags": tags or [],
        }
        r = await self._client_get().post("/bookmarks", json=payload)
        r.raise_for_status()
        data = r.json()
        return data[0] if isinstance(data, list) and data else {}

    async def list_bookmarks(self, user_id: str) -> list[dict]:
        if not self.configured:
            return []
        r = await self._client_get().get(
            "/bookmarks",
            params={"user_id": f"eq.{user_id}", "order": "created_at.desc"},
        )
        r.raise_for_status()
        return r.json()

    async def delete_bookmark(self, user_id: str, bookmark_id: str) -> None:
        if not self.configured:
            return
        r = await self._client_get().delete(
            "/bookmarks",
            params={"id": f"eq.{bookmark_id}", "user_id": f"eq.{user_id}"},
        )
        r.raise_for_status()


supabase_rest = SupabaseRest()
