"""Manual book requests.

A manual request is free-form text (title/author/narrator) typed by a user when
the release search finds nothing. It has no release row: an admin marks it
downloaded after adding the book by other means, or removes it.
"""

import uuid
from typing import Annotated

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Response,
    Security,
)
from pydantic import BaseModel
from sqlmodel import Session, asc, col, select

from app.internal.auth.authentication import AnyAuth, DetailedUser
from app.internal.models import EventEnum, GroupEnum, ManualBookRequest
from app.internal.notifications import send_all_manual_notifications
from app.util.db import get_session

router = APIRouter(prefix="/requests", tags=["Requests"])


class ManualRequest(BaseModel):
    title: str
    author: str
    narrator: str | None = None
    subtitle: str | None = None
    publish_date: str | None = None
    info: str | None = None


@router.post("/manual", status_code=201)
async def create_manual_request(
    body: ManualRequest,
    session: Annotated[Session, Depends(get_session)],
    background_task: BackgroundTasks,
    user: Annotated[DetailedUser, Security(AnyAuth())],
):
    book_request = ManualBookRequest(
        user_username=user.username,
        title=body.title,
        authors=body.author.split(","),
        narrators=body.narrator.split(",") if body.narrator else [],
        subtitle=body.subtitle,
        publish_date=body.publish_date,
        additional_info=body.info,
    )
    session.add(book_request)
    session.commit()

    background_task.add_task(
        send_all_manual_notifications,
        event_type=EventEnum.on_new_request,
        book_request=ManualBookRequest.model_validate(book_request),
    )
    return Response(status_code=201)


@router.get("/manual", response_model=list[ManualBookRequest])
async def list_manual_requests(
    session: Annotated[Session, Depends(get_session)],
    user: Annotated[DetailedUser, Security(AnyAuth())],
):
    return session.exec(
        select(ManualBookRequest)
        .where(
            user.is_admin() or ManualBookRequest.user_username == user.username,
            col(ManualBookRequest.user_username).is_not(None),
        )
        .order_by(asc(ManualBookRequest.downloaded))
    ).all()


@router.put("/manual/{id}", status_code=204)
async def update_manual_request(
    id: uuid.UUID,
    body: ManualRequest,
    session: Annotated[Session, Depends(get_session)],
    user: Annotated[DetailedUser, Security(AnyAuth())],
):
    book_request = session.get(ManualBookRequest, id)
    if not book_request:
        raise HTTPException(status_code=404, detail="Book request not found")

    if not user.is_admin() and book_request.user_username != user.username:
        raise HTTPException(status_code=403, detail="Not authorized")

    book_request.title = body.title
    book_request.subtitle = body.subtitle
    book_request.authors = body.author.split(",")
    book_request.narrators = body.narrator.split(",") if body.narrator else []
    book_request.publish_date = body.publish_date
    book_request.additional_info = body.info

    session.add(book_request)
    session.commit()
    return Response(status_code=204)


@router.patch("/manual/{id}/downloaded", status_code=204)
async def mark_manual_downloaded(
    id: uuid.UUID,
    session: Annotated[Session, Depends(get_session)],
    background_task: BackgroundTasks,
    _: Annotated[DetailedUser, Security(AnyAuth(GroupEnum.admin))],
):
    book_request = session.get(ManualBookRequest, id)
    if book_request:
        book_request.downloaded = True
        session.add(book_request)
        session.commit()

        background_task.add_task(
            send_all_manual_notifications,
            event_type=EventEnum.on_successful_download,
            book_request=ManualBookRequest.model_validate(book_request),
        )
        return Response(status_code=204)
    raise HTTPException(status_code=404, detail="Request not found")


@router.delete("/manual/{id}", status_code=204)
async def delete_manual_request(
    id: uuid.UUID,
    session: Annotated[Session, Depends(get_session)],
    _: Annotated[DetailedUser, Security(AnyAuth(GroupEnum.admin))],
):
    book = session.get(ManualBookRequest, id)
    if book:
        session.delete(book)
        session.commit()
        return Response(status_code=204)
    raise HTTPException(status_code=404, detail="Request not found")
