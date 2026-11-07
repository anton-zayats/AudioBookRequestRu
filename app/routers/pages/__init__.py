from fastapi import APIRouter

from . import (
    auth,
    index,
    init,
    legacy,
    login,
    requests,
    search,
    settings,
    static,
)

router = APIRouter()

router.include_router(auth.router)
router.include_router(index.router)
router.include_router(init.router)
router.include_router(legacy.router)
router.include_router(login.router)
router.include_router(requests.router)
router.include_router(search.router)
router.include_router(settings.router)
router.include_router(static.router)
