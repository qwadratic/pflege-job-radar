"""Inbound WhatsApp harness: answers Pflege leads as Valentina, using the board's own filters.

Contract and conversation design: docs/whatsapp.md. Transport is the Meta WhatsApp Cloud API
(app/wa/meta.py), the reply is decided deterministically (app/wa/brain.py) from the same job
snapshot GET /api/jobs serves (app/data.py), and every turn is persisted (app/wa/store.py).
"""
