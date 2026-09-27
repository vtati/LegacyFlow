"""Aggregate the v1 endpoint routers."""

from fastapi import APIRouter

from app.api.v1.endpoints import me

router = APIRouter()
router.include_router(me.router)
