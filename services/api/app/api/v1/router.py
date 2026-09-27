"""Aggregate the v1 endpoint routers."""

from fastapi import APIRouter

from app.api.v1.endpoints import me
from app.domains.ai.router import router as ai_router
from app.domains.assets.router import router as assets_router
from app.domains.decisions.router import router as decisions_router
from app.domains.documents.router import router as documents_router
from app.domains.escalation.router import router as escalation_router
from app.domains.feature_flags.router import router as feature_flags_router
from app.domains.files.router import router as files_router
from app.domains.household.router import router as household_router
from app.domains.intake.router import router as intake_router
from app.domains.invitations.router import router as invitations_router
from app.domains.matters.router import router as matters_router
from app.domains.nominations.router import router as nominations_router
from app.domains.privacy.router import router as privacy_router
from app.domains.review.router import router as review_router

router = APIRouter()
router.include_router(me.router)
router.include_router(matters_router)
router.include_router(household_router)
router.include_router(invitations_router)
router.include_router(nominations_router)
router.include_router(privacy_router)
router.include_router(intake_router)
router.include_router(decisions_router)
router.include_router(escalation_router)
router.include_router(feature_flags_router)
router.include_router(assets_router)
router.include_router(review_router)
router.include_router(documents_router)
router.include_router(ai_router)
router.include_router(files_router)
