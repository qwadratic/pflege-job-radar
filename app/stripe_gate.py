"""Stripe pay-per-closed-posting gate (stub; filled in by billing phase 2, feature-flagged until keys exist)."""
from fastapi import APIRouter

router = APIRouter()


@router.get("/stripe/status")
def stripe_status():
    return {"stub": True, "configured": False}
