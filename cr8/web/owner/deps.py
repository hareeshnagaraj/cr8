"""Shared request dependencies for owner routers."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse, Response

from ..common.auth import UserSession, user_session
from ..common.settings import AppSettings


def settings(request: Request) -> AppSettings:
    return request.app.state.settings


def session_or_401(
    request: Request, *, detail: str = "owner login required"
) -> UserSession:
    session = user_session(request, settings(request))
    if session is None:
        raise HTTPException(status_code=401, detail=detail)
    return session


def session_or_redirect(request: Request) -> UserSession | RedirectResponse:
    session = user_session(request, settings(request))
    if session is None:
        return RedirectResponse("/login", status_code=303)
    return session


def admin_or_403(
    request: Request,
    *,
    detail_401: str = "owner login required",
    detail_403: str = "admins only",
) -> UserSession:
    """Signed in AND role=owner. Everyone signed in can listen and tag; only
    admins manage who else gets in and rewrite the shared tag vocabulary."""
    session = session_or_401(request, detail=detail_401)
    if not session.is_admin:
        raise HTTPException(status_code=403, detail=detail_403)
    return session


def admin_or_redirect(request: Request) -> UserSession | RedirectResponse:
    session = session_or_redirect(request)
    if isinstance(session, RedirectResponse) or session.is_admin:
        return session
    return RedirectResponse("/", status_code=303)


def context(
    request: Request, session: UserSession, **values: Any
) -> dict[str, Any]:
    payload = {
        "request": request,
        "owner": session,
        "path": request.url.path,
    }
    payload.update(values)
    return payload


def set_session_cookie(
    response: Response, app_settings: AppSettings, raw_sid: str
) -> None:
    response.set_cookie(
        app_settings.cookie_name,
        raw_sid,
        max_age=30 * 86_400,
        httponly=True,
        secure=app_settings.cookie_secure,
        samesite="lax",
        path="/",
    )
