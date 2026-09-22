"""The refusal taxonomy (TASK-130, plan section 5.1).

``.status_code`` is load-bearing on our side: app/wa/luna/campaign.py classifies 4xx as ``failed``
(ownership restored, the recipient stays claimable) and anything else as ``uncertain`` (never
auto-resent). So the HTTP status is not decoration -- it decides whether a real person may be
messaged again. Every refusal in this package names one of these codes; there is no generic 500
raised by us on purpose.

| code              | HTTP | meaning on this rail                                              |
|-------------------|------|-------------------------------------------------------------------|
| invalid_request   | 400  | the request cannot be classified; nothing was attempted           |
| unauthorized      | 401  | bad or missing bearer token, or a non-loopback peer               |
| chat_not_found    | 404  | no chat by that identity is on the handset's list                 |
| idempotency_conflict | 409 | a different body under a key whose first body has no result yet |
| chat_identity_mismatch | 409 | the chat on screen is not the one the caller named           |
| not_on_whatsapp   | 422  | the chat could not be opened for that number                      |
| rail_parked       | 429  | the governor refused: quiet hours, Sunday, a cap, or the min gap  |
| executor_error    | 500  | a bug in this package                                             |
| device_unavailable| 503  | flock busy, adb gone, phone asleep -- nothing was typed           |
| send_unconfirmed  | 504  | keys may have been pressed and no tick was read. NEVER auto-resent|
| destruction_unverified | 504 | a clear/delete was tapped and the result could not be proved  |

THE 4xx CLASSIFICATION IS ABOUT SENDS ONLY. campaign.py reads these codes off ``POST /v1/messages``;
the three codes added for the chat operations (TASK-147) never reach it, because nothing destroys a
chat on a campaign's behalf.
"""


class BridgeRefusal(Exception):
    """A refusal that travels the wire as the contract's error envelope."""

    def __init__(self, code, status_code, message, *, retryable=False, detail=None):
        super().__init__(f"{code} ({status_code}): {message}")
        self.code = code
        self.status_code = status_code
        self.message = message
        self.retryable = retryable
        self.detail = detail or {}

    def envelope(self):
        body = {"code": self.code, "message": self.message, "http_status": self.status_code,
                "retryable": self.retryable}
        if self.detail:
            body["detail"] = self.detail
        return {"ok": False, "error": body}


def invalid_request(message, **detail):
    return BridgeRefusal("invalid_request", 400, message, detail=detail)


def unauthorized(message, **detail):
    return BridgeRefusal("unauthorized", 401, message, detail=detail)


def chat_not_found(message, **detail):
    return BridgeRefusal("chat_not_found", 404, message, detail=detail)


def idempotency_conflict(message, **detail):
    return BridgeRefusal("idempotency_conflict", 409, message, detail=detail)


def chat_identity_mismatch(message, **detail):
    # Nothing was tapped. The caller named a chat and the handset shows another one, or two rows
    # answer to the same name: either way the guess would be about which conversation to destroy.
    return BridgeRefusal("chat_identity_mismatch", 409, message, detail=detail)


def not_on_whatsapp(message, **detail):
    return BridgeRefusal("not_on_whatsapp", 422, message, detail=detail)


def rail_parked(message, **detail):
    # retryable: nothing went out, the caller may ask again after next_slot_at.
    return BridgeRefusal("rail_parked", 429, message, retryable=True, detail=detail)


def executor_error(message, **detail):
    return BridgeRefusal("executor_error", 500, message, detail=detail)


def device_unavailable(message, **detail):
    # retryable: this code is only ever raised where nothing was typed.
    return BridgeRefusal("device_unavailable", 503, message, retryable=True, detail=detail)


def send_unconfirmed(message, **detail):
    # NOT retryable, by definition. Only a reconcile verdict of confirmed_absent may authorise a
    # resend of this key; an automatic retry here is a duplicate message to a real candidate.
    return BridgeRefusal("send_unconfirmed", 504, message, retryable=False, detail=detail)


def destruction_unverified(message, **detail):
    # The same rule as send_unconfirmed, pointed the other way: the taps happened and the handset
    # did not show the result we were promised. The audit row is written before this is raised, so
    # the record exists even though the outcome is unknown. Not retryable without a human looking.
    return BridgeRefusal("destruction_unverified", 504, message, retryable=False, detail=detail)
