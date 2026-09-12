"""GET /api/wa/queue and GET /api/wa/queue/mailing-list -- read-only views over app/wa/queue.py's
tables. Owner-only (app/auth.py:OWNER_READ_PREFIXES), same PII class as GET /api/wa/threads: a
phone number plus the clinics it matched and what is known about them.

Neither route sends anything to a clinic -- this is a preview/report a human acts on, not a sender
(see app/wa/queue.py's module docstring and plan section 4's explicit "no email texts" scope).
"""
from fastapi import APIRouter

from . import queue as Q

router = APIRouter()


@router.get("/wa/queue")
def wa_queue():
    """(candidates) x (matched clinic subset): every consenting candidate, ranked clinics with
    contact email/source where known."""
    conn = Q.db()
    try:
        rows = Q.queue_rows(conn)
    finally:
        conn.close()
    return {"total": len(rows), "rows": rows}


@router.get("/wa/queue/mailing-list")
def wa_queue_mailing_list():
    """Flattened candidate x clinic x contact-email preview. A report to hand to a human, never an
    automatic send."""
    conn = Q.db()
    try:
        rows = Q.mailing_list_rows(conn)
    finally:
        conn.close()
    clinics_with_contact = {r["clinic_id"] for r in rows if r.get("contact_email")}
    candidates = {r["phone"] for r in rows}
    return {"total": len(rows), "candidates": len(candidates), "clinics_with_contact": len(clinics_with_contact),
            "rows": rows}
