"""Setup, login, logout, and members HTML."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from ..common.auth import (
    AuthError,
    authenticate_user,
    create_member,
    create_owner,
    create_user_session,
    destroy_session,
    user_exists,
    user_session,
)
from ..common.database import fetch_all, fetch_one, mutate, reading
from ..common.settings import AppSettings
from ..common.templates import make_templates
from .deps import (
    admin_or_403,
    admin_or_redirect,
    context as _context,
    set_session_cookie,
    settings as get_settings,
)
from .routes_assignments import clear_for_removed_member


router = APIRouter()
templates = make_templates(Path(__file__).parent / "templates")

@router.get("/setup", response_class=HTMLResponse)
def setup_page(request: Request) -> Response:
    settings = get_settings(request)
    if user_exists(settings):
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(
        request=request,
        name="owner/setup.html",
        context={"request": request, "error": None},
    )


@router.post("/setup", response_class=HTMLResponse)
async def setup_owner(request: Request) -> Response:
    settings = get_settings(request)
    form = await request.form()
    try:
        user_id = create_owner(
            settings,
            username=str(form.get("username", "")),
            display=str(form.get("display", "")),
            password=str(form.get("password", "")),
        )
        raw_sid, _ = create_user_session(settings, user_id)
    except AuthError:
        return templates.TemplateResponse(
            request=request,
            name="owner/setup.html",
            context={"request": request, "error": "Setup could not be completed."},
            status_code=400,
        )
    response = RedirectResponse("/", status_code=303)
    set_session_cookie(response, settings, raw_sid)
    return response


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request) -> Response:
    settings = get_settings(request)
    if not user_exists(settings):
        return RedirectResponse("/setup", status_code=303)
    if user_session(request, get_settings(request)):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(
        request=request,
        name="owner/login.html",
        context={"request": request, "error": None},
    )


@router.post("/login", response_class=HTMLResponse)
async def login(request: Request) -> Response:
    settings = get_settings(request)
    form = await request.form()
    try:
        user = authenticate_user(
            settings,
            str(form.get("username", "")),
            str(form.get("password", "")),
        )
        raw_sid, _ = create_user_session(settings, int(user["id"]))
    except AuthError:
        return templates.TemplateResponse(
            request=request,
            name="owner/login.html",
            context={"request": request, "error": "Login failed."},
            status_code=401,
        )
    response = RedirectResponse("/", status_code=303)
    set_session_cookie(response, settings, raw_sid)
    return response


@router.post("/logout")
def logout(request: Request) -> RedirectResponse:
    settings = get_settings(request)
    destroy_session(settings, request.cookies.get(settings.cookie_name))
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(settings.cookie_name, path="/")
    return response


def _member_rows(settings: AppSettings) -> list[dict[str, Any]]:
    with reading(settings.db_path) as connection:
        return [
            dict(row)
            for row in fetch_all(
                connection,
                """
                SELECT id, username, display, created_at
                FROM users
                ORDER BY username COLLATE NOCASE
                """,
            )
        ]


@router.get("/members", response_class=HTMLResponse)
def members_page(request: Request) -> Response:
    session = admin_or_redirect(request)
    if isinstance(session, RedirectResponse):
        return session
    return templates.TemplateResponse(
        request=request,
        name="owner/members.html",
        context=_context(
            request,
            session,
            members=_member_rows(get_settings(request)),
            generated=None,
            error=None,
        ),
    )


@router.post("/members", response_class=HTMLResponse)
async def add_member(request: Request) -> Response:
    session = admin_or_403(request)
    form = await request.form()
    try:
        generated = create_member(
            get_settings(request),
            username=str(form.get("username", "")),
            display=str(form.get("display", "")),
        )
    except AuthError as exc:
        return templates.TemplateResponse(
            request=request,
            name="owner/members.html",
            context=_context(
                request,
                session,
                members=_member_rows(get_settings(request)),
                generated=None,
                error=str(exc),
            ),
            status_code=400,
        )
    return templates.TemplateResponse(
        request=request,
        name="owner/members.html",
        context=_context(
            request,
            session,
            members=_member_rows(get_settings(request)),
            generated=generated,
            error=None,
        ),
        status_code=201,
    )


@router.post("/members/{user_id}/remove")
def remove_member(request: Request, user_id: int) -> RedirectResponse:
    session = admin_or_403(request)
    if user_id == session.user_id:
        raise HTTPException(
            status_code=400, detail="you cannot remove your own account"
        )

    def remove(connection: Any) -> None:
        member = fetch_one(
            connection, "SELECT id, username FROM users WHERE id=?", (user_id,)
        )
        if member is None:
            raise HTTPException(status_code=404, detail="member unavailable")
        clear_for_removed_member(connection, str(member["username"]))
        connection.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
        connection.execute("DELETE FROM users WHERE id=?", (user_id,))

    mutate(get_settings(request).db_path, remove)
    return RedirectResponse("/members", status_code=303)

