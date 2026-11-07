from typing import Annotated

from fastapi import APIRouter, Depends, Security
from sqlmodel import Session

from app.internal.auth.authentication import ABRAuth, DetailedUser
from app.internal.releases import popular_releases
from app.util.db import get_session
from app.util.templates import catalog_response

router = APIRouter()


@router.get("/")
def read_root(
    user: Annotated[DetailedUser, Security(ABRAuth())],
    session: Annotated[Session, Depends(get_session)],
):
    # hide the section when the same query used by /hx-popular would return nothing
    show_popular = bool(
        popular_releases(session, limit=1, exclude_username=user.username)
    )
    return catalog_response("Index.Index", user=user, show_popular=show_popular)


@router.get("/hx-popular")
async def get_popular_releases(
    session: Annotated[Session, Depends(get_session)],
    user: Annotated[DetailedUser, Security(ABRAuth())],
    limit: int = 10,
):
    cards = popular_releases(session, limit=limit, exclude_username=user.username)
    return catalog_response(
        "Index.PopularSection",
        title="Popular releases",
        user=user,
        cards=cards,
        description="Releases other users have requested",
        empty="No requests yet. Search for a release and add it to your requests.",
    )
