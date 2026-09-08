"""Owner / tailnet / magic-link identity (stub; filled in by billing phase 2). GET /api/me tells the pages who is visiting."""
from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/me")
def api_me(request: Request):
    email = request.headers.get("x-exedev-email")
    return {"stub": True, "email": email, "role": "anonymous"}
