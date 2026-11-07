from fastapi import APIRouter

from . import (
    auth,
    index,
    init,
    login,
    request,
    requests,
    search,
    settings,
    static,
    wishlist,
)

router = APIRouter()

router.include_router(auth.router)
router.include_router(index.router)
router.include_router(init.router)
router.include_router(login.router)
router.include_router(request.router)
router.include_router(requests.router)
router.include_router(search.router)
router.include_router(settings.router)
router.include_router(static.router)
router.include_router(wishlist.router)
