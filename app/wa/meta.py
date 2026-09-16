"""Meta WhatsApp Cloud API: webhook trust boundary and the two sends this harness needs.

Signature and challenge verification are copied from the production client
(apps/connectors/meta_whatsapp_cloud.py on tasker-dispatcher-01) because they *are* the inbound
trust boundary; the client itself is trimmed to text and reply buttons. urllib, not requests, so the
transport can be swapped for a fake in tests (``transport=``) without a network stub.
"""
import hashlib
import hmac
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from . import config as C


class MetaError(RuntimeError):
    """A Meta call failed. Carries the status and the parsed body so a log line can name the cause."""

    def __init__(self, message, status_code=None, payload=None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


def validate_webhook_signature(raw_body, signature_header, app_secret):
    """X-Hub-Signature-256 over the *raw* bytes. No secret or no header means no, never yes."""
    if not app_secret or not signature_header:
        return False
    header = str(signature_header).strip()
    if not header.lower().startswith("sha256="):
        return False
    expected = header.split("=", 1)[1].strip()
    digest = hmac.new(app_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, expected)


def verify_webhook_challenge(mode, token, challenge, verify_token):
    """Meta's GET handshake: echo hub.challenge only for mode=subscribe with the right token."""
    if mode != "subscribe" or not challenge:
        return None
    if not verify_token or token != verify_token:
        return None
    return str(challenge)


def sender_e164(sender):
    """Meta sends the wa_id as bare digits; the harness keys threads on +digits."""
    digits = re.sub(r"\D", "", str(sender or ""))
    return "+" + digits if digits else ""


def canonicalize_phone(raw, default_country_code=None):
    """One identity per human: '0170…', '0049170…', '+49170…' and '49170…' all become '+49170…'."""
    text = str(raw or "").strip()
    if not text:
        return ""
    cc = re.sub(r"\D", "", default_country_code or C.DEFAULT_COUNTRY_CODE) or "49"
    if text.startswith("+"):
        digits = re.sub(r"\D", "", text)
    elif text.startswith("00"):
        digits = re.sub(r"\D", "", text[2:])
    else:
        digits = re.sub(r"\D", "", text)
        if digits.startswith("0") and len(digits) >= 10:
            digits = cc + digits[1:]
        elif len(digits) <= 11 and not digits.startswith(cc):
            digits = cc + digits
    return "+" + digits if digits else ""


def _default_transport(method, url, headers=None, data=None, timeout=C.HTTP_TIMEOUT_SEC):
    req = urllib.request.Request(url=url, data=data, method=method.upper())
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            status = getattr(resp, "status", 200)
    except urllib.error.HTTPError as exc:
        body = exc.read()
        try:
            payload = json.loads(body.decode("utf-8")) if body else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            payload = {"raw": body.decode("utf-8", errors="replace")}
        raise MetaError(f"Meta HTTP {exc.code}", status_code=exc.code, payload=payload) from exc
    except urllib.error.URLError as exc:
        raise MetaError(f"Meta network error: {exc.reason}") from exc
    try:
        return json.loads(body.decode("utf-8")) if body else {}
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {"raw": body.decode("utf-8", errors="replace"), "status_code": status}


def _default_binary_transport(method, url, headers=None, timeout=C.HTTP_TIMEOUT_SEC):
    """Same shape as ``_default_transport``'s request side, but returns the response body as raw
    bytes, never parsed -- ``_default_transport`` always hands back a parsed dict (JSON) or text
    decoded with ``errors="replace"``, either of which would corrupt binary media content. Used
    only by ``Client.download_media`` (the media-id lookup itself is plain JSON and still goes
    through the regular ``transport``)."""
    req = urllib.request.Request(url=url, method=method.upper())
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        body = exc.read()
        raise MetaError(f"Meta HTTP {exc.code}", status_code=exc.code,
                        payload={"raw": body.decode("utf-8", errors="replace")[:500]}) from exc
    except urllib.error.URLError as exc:
        raise MetaError(f"Meta network error: {exc.reason}") from exc


# --- Templates: definition, send parameters, rendering (TASK-98) -----------------------------------
#
# The definition (Client.get_template / find_template) is the source of truth. The caller supplies
# values only, as one JSON-shaped mapping:
#
#   {"header": <variables> | {"id"|"link", "filename"?} | {"latitude", "longitude", "name"?, "address"?}
#              | {"product_retailer_id", "catalog_id"},
#    "body": <variables>,
#    "buttons": {"<index>": <button values>},
#    "limited_time_offer": {"expiration_time_ms"},
#    "carousel": [{"header", "body", "buttons"}, ...one per card],
#    "tap_target_configuration": [{"url", "title"}]}
#
# <variables>: a list (POSITIONAL, {{1}} first) or a mapping {"1"|"first_name": value}. A value is
# text (str/int) or, in a body, {"currency": {fallback_value, code, amount_1000}} /
# {"date_time": {fallback_value}}. Button values by definition type: QUICK_REPLY {payload}, URL
# <variables> when the url has one, OTP {code}, COPY_CODE {coupon_code}, FLOW {flow_token,
# flow_action_data}, CATALOG {thumbnail_product_retailer_id}, MPM {thumbnail_product_retailer_id,
# sections}, VOICE_CALL {ttl_minutes, payload}; PHONE_NUMBER and SPM take none.
# Send shapes: developers.facebook.com/documentation/business-messaging/whatsapp/templates/components
# and the per-feature template pages (LTO, carousel, flows, catalogs, calling, tap target).

TEMPLATE_FIELDS = ("id", "name", "language", "status", "category", "sub_category", "parameter_format",
                   "components")
_VAR_RE = re.compile(r"\{\{([A-Za-z0-9_]+)\}\}")
_TOP_KEYS = ("header", "body", "buttons", "limited_time_offer", "carousel", "tap_target_configuration")
_CARD_KEYS = ("header", "body", "buttons")
# button type -> (allowed value keys, required value keys, Meta sub_type). URL is handled apart
# (its values are template variables).
_BUTTON_SPECS = {
    "QUICK_REPLY": ({"payload"}, set(), "quick_reply"),
    "OTP": ({"code"}, {"code"}, "url"),
    "COPY_CODE": ({"coupon_code"}, {"coupon_code"}, "copy_code"),
    "FLOW": ({"flow_token", "flow_action_data"}, set(), "flow"),
    "CATALOG": ({"thumbnail_product_retailer_id"}, set(), "catalog"),
    "MPM": ({"thumbnail_product_retailer_id", "sections"}, {"thumbnail_product_retailer_id", "sections"}, "mpm"),
    "VOICE_CALL": ({"ttl_minutes", "payload"}, set(), "voice_call"),
    "PHONE_NUMBER": (set(), set(), None),
    "SPM": (set(), set(), None),
}


class TemplateParamsError(MetaError):
    """Supplied values do not fit the template definition. ``problems`` names every missing, extra
    or invalid variable. Raised before any POST."""

    def __init__(self, template_name, problems):
        super().__init__(f"template {template_name!r}: " + "; ".join(problems))
        self.problems = list(problems)


def template_variables(text):
    """Placeholder names in order of appearance: ['1', '2'] or ['first_name']."""
    return _VAR_RE.findall(text or "")


def ensure_approved(definition):
    """Meta only sends APPROVED templates; anything else is a loud error here, not a failed POST."""
    status = str(definition.get("status") or "")
    if status != "APPROVED":
        raise MetaError(f"template {definition.get('name')!r} (id {definition.get('id')}) is "
                        f"{status or 'without a status'}, not APPROVED", payload=definition)
    return definition


def _filled(value):
    if isinstance(value, str):
        return bool(value.strip())
    return value is not None and value != [] and value != {}


class _TemplateWalk:
    """One pass over a definition that binds the supplied values, builds Meta's send components and
    the recipient view together, and collects every problem. Build and render share it, so a
    stored text cannot drift from what was posted."""

    def __init__(self, definition):
        self.fmt = str(definition.get("parameter_format") or "").upper()
        self.problems = []

    def problem(self, msg):
        if msg not in self.problems:
            self.problems.append(msg)

    def keys(self, where, supplied, allowed, required):
        """Mapping of fixed keys: report extra keys, empty values and absent required keys."""
        if supplied is None:
            supplied = {}
        if not isinstance(supplied, dict):
            self.problem(f"{where}: expected a mapping, got {type(supplied).__name__}")
            return None
        for key, value in supplied.items():
            if key not in allowed:
                self.problem(f"{where}: extra key {key!r}" + (f" (allowed: {', '.join(sorted(allowed))})"
                                                               if allowed else " (takes no values)"))
            elif not _filled(value):
                self.problem(f"{where}: empty {key}")
        for key in sorted(required):
            if key not in supplied:
                self.problem(f"{where}: missing {key}")
        return {k: v for k, v in supplied.items() if k in allowed and _filled(v)}

    def value(self, where, value, typed):
        if isinstance(value, bool) or value is None:
            self.problem(f"{where}: invalid value {value!r}")
            return None, None
        if isinstance(value, int):
            value = str(value)
        if isinstance(value, str):
            if not value.strip():
                self.problem(f"{where}: empty value")
                return None, None
            return {"type": "text", "text": value}, value
        if typed and isinstance(value, dict) and len(value) == 1 and next(iter(value)) in ("currency", "date_time"):
            kind = next(iter(value))
            obj = value[kind]
            need = ("fallback_value", "code", "amount_1000") if kind == "currency" else ("fallback_value",)
            missing = [k for k in need if not isinstance(obj, dict) or not _filled(obj.get(k))]
            if missing:
                self.problem(f"{where}: {kind} needs {', '.join(missing)}")
                return None, None
            return {"type": kind, kind: obj}, str(obj["fallback_value"])
        self.problem(f"{where}: unsupported value {value!r} (text"
                     + (", {currency: ...} or {date_time: ...})" if typed else ")"))
        return None, None

    def variables(self, where, text, supplied, typed):
        """Bind a list/mapping to the placeholders in ``text``: (parameters, rendered text)."""
        names = list(dict.fromkeys(template_variables(text)))
        if not names:
            if supplied not in (None, [], {}):
                self.problem(f"{where}: template text has no variables, got {supplied!r}")
            return [], text
        if self.fmt not in ("NAMED", "POSITIONAL"):
            self.problem(f"definition parameter_format is {self.fmt or 'missing'}, need NAMED or POSITIONAL")
            return [], text
        for n in names:
            if n.isdigit() != (self.fmt == "POSITIONAL"):
                self.problem(f"{where}: placeholder {{{{{n}}}}} does not fit parameter_format {self.fmt}")
        if supplied is None:
            bound = {}
        elif isinstance(supplied, (list, tuple)):
            if self.fmt != "POSITIONAL":
                self.problem(f"{where}: NAMED template needs a mapping {{name: value}}, got a list")
                bound = {}
            else:
                bound = {str(i + 1): v for i, v in enumerate(supplied)}
        elif isinstance(supplied, dict):
            bound = {str(k): v for k, v in supplied.items()}
        else:
            self.problem(f"{where}: variables must be a list or a mapping, got {type(supplied).__name__}")
            bound = {}
        for n in names:
            if n not in bound:
                self.problem(f"{where}: missing variable {{{{{n}}}}}")
        for k in bound:
            if k not in names:
                self.problem(f"{where}: extra variable {{{{{k}}}}} not in template")
        if self.fmt == "POSITIONAL" and all(n.isdigit() for n in names):
            names = sorted(names, key=int)
        parameters, shown = [], {}
        for n in names:
            if n not in bound:
                continue
            param, display = self.value(f"{where} {{{{{n}}}}}", bound[n], typed)
            if param is None:
                continue
            if self.fmt == "NAMED":
                kind = param.pop("type")
                param = {"type": kind, "parameter_name": n, **param}
            parameters.append(param)
            shown[n] = display
        return parameters, _VAR_RE.sub(lambda m: shown.get(m.group(1), m.group(0)), text)

    def header(self, where, comp, supplied):
        fmt = str(comp.get("format") or "").upper()
        if fmt == "TEXT":
            parameters, text = self.variables(where, comp.get("text") or "", supplied, typed=False)
            return parameters, {"format": "TEXT", "text": text}
        if fmt in ("IMAGE", "VIDEO", "DOCUMENT"):
            allowed = {"id", "link", "filename"} if fmt == "DOCUMENT" else {"id", "link"}
            if supplied is None:
                self.problem(f"{where}: missing {fmt} media (id or link)")
                return [], {"format": fmt}
            media = self.keys(where, supplied, allowed, set())
            if media is None:
                return [], {"format": fmt}
            sources = [k for k in ("id", "link") if k in media]
            if len(sources) != 1:
                self.problem(f"{where}: {fmt} media needs exactly one of id or link, got "
                             f"{' and '.join(sources) or 'neither'}")
                return [], {"format": fmt}
            return [{"type": fmt.lower(), fmt.lower(): media}], {"format": fmt, fmt.lower(): media}
        if fmt == "LOCATION":
            loc = self.keys(where, supplied, {"latitude", "longitude", "name", "address"}, {"latitude", "longitude"})
            if loc is None or not {"latitude", "longitude"} <= set(loc):
                return [], {"format": fmt}
            return [{"type": "location", "location": loc}], {"format": fmt, "location": loc}
        if fmt == "PRODUCT":
            need = {"product_retailer_id", "catalog_id"}
            product = self.keys(where, supplied, need, need)
            if product is None or set(product) != need:
                return [], {"format": fmt}
            return [{"type": "product", "product": product}], {"format": fmt, "product": product}
        if fmt == "GIF":
            self.problem(f"{where}: GIF headers send only through the Marketing Messages API, not Cloud API /messages")
            return [], {"format": fmt}
        self.problem(f"{where}: unsupported header format {fmt or 'missing'}")
        return [], {"format": fmt}

    def button(self, where, index, btn, supplied):
        btype = str(btn.get("type") or "").upper()
        view = {"type": btype, "text": btn.get("text")}
        if btype == "URL":
            parameters, url = self.variables(where, btn.get("url") or "", supplied, typed=False)
            view["url"] = url
            comp = ({"type": "button", "sub_type": "url", "index": index, "parameters": parameters}
                    if parameters else None)
            return comp, view
        spec = _BUTTON_SPECS.get(btype)
        if spec is None:
            self.problem(f"{where}: unsupported button type {btype or 'missing'}")
            return None, view
        allowed, required, sub_type = spec
        values = self.keys(where, supplied, allowed, required)
        if btype == "PHONE_NUMBER":
            view["phone_number"] = btn.get("phone_number")
        elif btype == "QUICK_REPLY":
            view["payload"] = None
        elif btype in ("OTP", "COPY_CODE"):
            view["code"] = None
        if not values or not required <= set(values):
            return None, view
        if btype == "QUICK_REPLY":
            view["payload"] = values["payload"]
            parameters = [{"type": "payload", "payload": values["payload"]}]
        elif btype == "OTP":
            view["code"] = values["code"]
            parameters = [{"type": "text", "text": str(values["code"])}]
        elif btype == "COPY_CODE":
            view["code"] = values["coupon_code"]
            parameters = [{"type": "coupon_code", "coupon_code": values["coupon_code"]}]
        elif btype == "VOICE_CALL":
            view.update(values)
            parameters = [{"type": k, k: values[k]} for k in ("ttl_minutes", "payload") if k in values]
        else:   # FLOW, CATALOG, MPM
            parameters = [{"type": "action", "action": values}]
        return {"type": "button", "sub_type": sub_type, "index": index, "parameters": parameters}, view

    def components(self, defs, params, prefix, allowed_keys):
        if params is None:
            params = {}
        if not isinstance(params, dict):
            self.problem(f"{prefix or 'params'}: expected a mapping of {', '.join(allowed_keys)}, "
                         f"got {type(params).__name__}")
            params = {}
        for key in params:
            if key not in allowed_keys:
                self.problem(f"{prefix}{key}: unknown key (allowed: {', '.join(allowed_keys)})")
        out, used = [], set()
        view = {"header": None, "limited_time_offer": None, "body": None, "footer": None, "buttons": [],
                "carousel": None, "call_permission_request": False}
        for comp in defs:
            ctype = str(comp.get("type") or "").upper()
            if ctype == "HEADER":
                used.add("header")
                parameters, view["header"] = self.header(f"{prefix}header", comp, params.get("header"))
                if parameters:
                    out.append({"type": "header", "parameters": parameters})
            elif ctype == "BODY":
                used.add("body")
                parameters, view["body"] = self.variables(f"{prefix}body", comp.get("text") or "",
                                                          params.get("body"), typed=True)
                if parameters:
                    out.append({"type": "body", "parameters": parameters})
            elif ctype == "FOOTER":
                view["footer"] = comp.get("text")
            elif ctype == "BUTTONS":
                used.add("buttons")
                buttons = comp.get("buttons") or []
                supplied = params.get("buttons")
                if supplied is None:
                    supplied = {}
                if not isinstance(supplied, dict):
                    self.problem(f"{prefix}buttons: expected a mapping {{index: values}}, got {type(supplied).__name__}")
                    supplied = {}
                bound = {}
                for key, values in supplied.items():
                    k = str(key)
                    if not k.isdigit() or int(k) >= len(buttons):
                        self.problem(f"{prefix}buttons[{k}]: template has no button at this index "
                                     f"({len(buttons)} buttons)")
                    elif int(k) in bound:
                        self.problem(f"{prefix}buttons[{k}]: given twice")
                    else:
                        bound[int(k)] = values
                for i, btn in enumerate(buttons):
                    comp_out, btn_view = self.button(f"{prefix}buttons[{i}]", i, btn, bound.get(i))
                    view["buttons"].append(btn_view)
                    if comp_out:
                        out.append(comp_out)
            elif ctype == "LIMITED_TIME_OFFER":
                used.add("limited_time_offer")
                lto = comp.get("limited_time_offer") or {}
                need = {"expiration_time_ms"} if lto.get("has_expiration") else set()
                values = self.keys(f"{prefix}limited_time_offer", params.get("limited_time_offer"), need, need)
                ms = (values or {}).get("expiration_time_ms")
                if ms is not None and (isinstance(ms, bool) or not isinstance(ms, int)):
                    self.problem(f"{prefix}limited_time_offer: expiration_time_ms must be an integer, got {ms!r}")
                    values = None
                view["limited_time_offer"] = {"text": lto.get("text"),
                                              "expiration_time_ms": (values or {}).get("expiration_time_ms")}
                if values and need <= set(values) and need:
                    out.append({"type": "limited_time_offer", "parameters": [
                        {"type": "limited_time_offer", "limited_time_offer": values}]})
            elif ctype == "CAROUSEL":
                used.add("carousel")
                cards = comp.get("cards") or []
                supplied = params.get("carousel")
                if supplied is None:
                    supplied = []
                if not isinstance(supplied, (list, tuple)):
                    self.problem(f"{prefix}carousel: expected a list of cards, got {type(supplied).__name__}")
                    supplied = []
                if len(supplied) != len(cards):
                    self.problem(f"{prefix}carousel: template has {len(cards)} cards, got {len(supplied)}")
                card_out, card_views = [], []
                for i, card in enumerate(cards):
                    comps, card_view = self.components(card.get("components") or [],
                                                       supplied[i] if i < len(supplied) else None,
                                                       f"{prefix}carousel[{i}].", _CARD_KEYS)
                    card_out.append({"card_index": i, "components": comps})
                    card_views.append({k: card_view[k] for k in ("header", "body", "buttons")})
                out.append({"type": "carousel", "cards": card_out})
                view["carousel"] = card_views
            elif ctype == "CALL_PERMISSION_REQUEST":
                view["call_permission_request"] = True
            else:
                self.problem(f"{prefix}components: unsupported component type {ctype or 'missing'}")
        for key in params:
            if key in allowed_keys and key not in used and key != "tap_target_configuration":
                self.problem(f"{prefix}{key}: template has no {key.upper()} component")
        return out, view


def _walk_template(definition, params):
    if not isinstance(definition, dict) or not definition.get("name") or not isinstance(definition.get("components"), list):
        raise MetaError("a template definition needs name and components (Client.get_template)", payload=definition)
    walk = _TemplateWalk(definition)
    components, view = walk.components(definition["components"], params, "", _TOP_KEYS)
    # Send-only component: overrides the title of the message's tap-target URL, not in the definition.
    tap = params.get("tap_target_configuration") if isinstance(params, dict) else None
    view["tap_target_configuration"] = None
    if tap is not None:
        if not isinstance(tap, (list, tuple)) or not tap:
            walk.problem("tap_target_configuration: expected a non-empty list of {url, title}")
        else:
            before = len(walk.problems)
            rows = [walk.keys(f"tap_target_configuration[{i}]", row, {"url", "title"}, {"url", "title"})
                    for i, row in enumerate(tap)]
            if len(walk.problems) == before:
                components.append({"type": "tap_target_configuration", "parameters": [
                    {"type": "tap_target_configuration", "tap_target_configuration": rows}]})
                view["tap_target_configuration"] = rows
    return components, view, walk.problems


def validate_template_params(definition, params):
    """Every problem with ``params`` against ``definition``, as readable strings; empty means fit."""
    return _walk_template(definition, params)[2]


def build_template_components(definition, params):
    """Meta's ``template.components`` for these values. Raises TemplateParamsError naming every
    missing/extra/invalid variable."""
    components, _view, problems = _walk_template(definition, params)
    if problems:
        raise TemplateParamsError(definition.get("name"), problems)
    return components


def _flat_header(header):
    fmt = header["format"]
    if fmt == "TEXT":
        return header["text"]
    if fmt in ("IMAGE", "VIDEO", "DOCUMENT"):
        media = header[fmt.lower()]
        source = media.get("link") or f"media id {media.get('id')}"
        return f"[{fmt.lower()}: {media['filename']} {source}]" if media.get("filename") else f"[{fmt.lower()}: {source}]"
    if fmt == "LOCATION":
        loc = header["location"]
        label = ", ".join(str(loc[k]) for k in ("name", "address") if k in loc)
        return f"[location: {label + ' ' if label else ''}({loc['latitude']}, {loc['longitude']})]"
    return f"[product: {header['product']['product_retailer_id']}]"


def _flat_button(btn):
    label = btn.get("text")
    if btn["type"] == "URL":
        return f"[{label} -> {btn['url']}]"
    if btn["type"] == "PHONE_NUMBER":
        return f"[{label} -> tel:{btn.get('phone_number')}]"
    if btn["type"] in ("COPY_CODE", "OTP"):
        return f"[{label or 'Copy code'}: {btn['code']}]" if btn.get("code") else f"[{label or 'Copy code'}]"
    return f"[{label or btn['type'].lower()}]"


def _flat_sections(view):
    sections = []
    if view["header"]:
        sections.append(_flat_header(view["header"]))
    if view["limited_time_offer"]:
        lto = view["limited_time_offer"]
        expires = lto.get("expiration_time_ms")
        until = (", expires " + datetime.fromtimestamp(int(expires) / 1000, tz=timezone.utc).isoformat()
                 if expires else "")
        sections.append(f"[offer: {lto.get('text')}{until}]")
    if view["body"] is not None:
        sections.append(view["body"])
    if view["footer"]:
        sections.append(view["footer"])
    if view["call_permission_request"]:
        sections.append("[call permission request]")
    if view["buttons"]:
        sections.append("\n".join(_flat_button(b) for b in view["buttons"]))
    for i, card in enumerate(view.get("carousel") or []):
        card_view = dict(card, limited_time_offer=None, footer=None, call_permission_request=False, carousel=None)
        sections.append(f"[card {i + 1}]\n" + "\n\n".join(_flat_sections(card_view)))
    for row in view.get("tap_target_configuration") or []:
        sections.append(f"[{row['title']} -> {row['url']}]")
    return sections


def render_template(definition, params):
    """What the recipient sees: {name, language, header, limited_time_offer, body, footer, buttons,
    carousel, call_permission_request, tap_target_configuration, text}. ``text`` is the flat form
    stored on the outbound row: sections split by a blank line, media/location/buttons as [..].
    A QUICK_REPLY without a supplied payload renders payload None (Meta's webhook then carries the
    label as payload). Raises TemplateParamsError like build_template_components."""
    _components, view, problems = _walk_template(definition, params)
    if problems:
        raise TemplateParamsError(definition.get("name"), problems)
    return {"name": definition.get("name"), "language": definition.get("language"), **view,
            "text": "\n\n".join(_flat_sections(view))}


class Client:
    """Outbound half of the Cloud API. One instance per request; state lives in SQLite, not here."""

    def __init__(self, transport=None, media_transport=None, access_token=None, phone_number_id=None):
        self.transport = transport or _default_transport
        self.media_transport = media_transport or _default_binary_transport
        self.access_token = C.ACCESS_TOKEN if access_token is None else access_token
        self.phone_number_id = C.PHONE_NUMBER_ID if phone_number_id is None else phone_number_id

    def _post(self, payload):
        if not self.access_token or not self.phone_number_id:
            raise MetaError("META_WHATSAPP_ACCESS_TOKEN / META_WHATSAPP_PHONE_NUMBER_ID are not set")
        url = f"https://graph.facebook.com/{C.GRAPH_API_VERSION}/{self.phone_number_id}/messages"
        body = json.dumps(payload).encode("utf-8")
        headers = {"Authorization": "Bearer " + self.access_token, "Content-Type": "application/json"}
        out = self.transport(method="POST", url=url, headers=headers, data=body)
        wamid = (((out or {}).get("messages") or [{}])[0] or {}).get("id")
        if not wamid:
            # The call came back 2xx without a message id: we do not know whether it went out, and
            # saying "sent" here is the one lie that would corrupt the thread. Fail loudly instead.
            raise MetaError("Meta accepted the call but returned no message id", payload=out)
        return wamid

    def send_text(self, to_e164, body):
        return self._post({"messaging_product": "whatsapp", "to": re.sub(r"\D", "", to_e164),
                           "type": "text", "text": {"body": body}})

    def send_buttons(self, to_e164, body, buttons):
        """Reply buttons: Meta allows at most 3, titles at most 20 characters. Both are hard errors
        here rather than a silent trim, because a trimmed title changes what the candidate answers."""
        if not buttons:
            raise MetaError("send_buttons needs at least one button")
        if len(buttons) > 3:
            raise MetaError(f"Meta allows 3 reply buttons, got {len(buttons)}")
        rows = []
        for b in buttons:
            bid, title = str(b.get("id") or "").strip(), str(b.get("title") or "").strip()
            if not bid or not title:
                raise MetaError(f"button needs id and title: {b!r}")
            if len(title) > 20:
                raise MetaError(f"button title over 20 chars: {title!r}")
            rows.append({"type": "reply", "reply": {"id": bid, "title": title}})
        return self._post({"messaging_product": "whatsapp", "recipient_type": "individual",
                           "to": re.sub(r"\D", "", to_e164), "type": "interactive",
                           "interactive": {"type": "button", "body": {"text": body},
                                           "action": {"buttons": rows}}})

    def send_template(self, to_e164, template_name=None, language=None, params=None, *, definition=None):
        """A pre-approved template message: the only kind Meta accepts once the 24h customer-service
        free-form window has closed (WhatsApp Cloud API policy -- see app/wa/api.py:_freeform_window_open).

        Without ``definition`` (legacy): ``template_name`` + ``language`` (default "de") and
        ``params`` as an ordered list of body-variable strings, nothing validated locally.
        With ``definition`` (``get_template``/``find_template``): ``params`` is the structured value
        set described above ``TEMPLATE_FIELDS``; header, body, buttons, LTO, carousel and tap target
        are built from the definition and every missing/extra variable raises TemplateParamsError
        before the POST. ``template_name``/``language`` may be omitted; if given they must match."""
        if definition is None:
            if not template_name:
                raise MetaError("send_template needs a template_name")
            if params is not None and not isinstance(params, (list, tuple)):
                raise MetaError("structured template params need definition= (Client.get_template)")
            template = {"name": template_name, "language": {"code": language or "de"}}
            if params:
                template["components"] = [{"type": "body",
                                           "parameters": [{"type": "text", "text": str(p)} for p in params]}]
        else:
            if not definition.get("language"):
                raise MetaError(f"template definition {definition.get('name')!r} has no language", payload=definition)
            for label, given, defined in (("template_name", template_name, definition.get("name")),
                                          ("language", language, definition.get("language"))):
                if given is not None and given != defined:
                    raise MetaError(f"send_template {label}={given!r} does not match the definition's {defined!r}")
            components = build_template_components(definition, params)
            template = {"name": definition["name"], "language": {"code": definition["language"]}}
            if components:
                template["components"] = components
        return self._post({"messaging_product": "whatsapp", "to": re.sub(r"\D", "", to_e164),
                           "type": "template", "template": template})

    def get_template(self, template_id, require_approved=False, fields=TEMPLATE_FIELDS):
        """``GET /{template_id}?fields=...`` -- read-only lookup of one template by its Meta id (what a
        colleague hands over from WhatsApp Manager). ``require_approved=True`` raises unless the
        status is APPROVED."""
        if not self.access_token:
            raise MetaError("META_WHATSAPP_ACCESS_TOKEN is not set")
        if not str(template_id or "").strip():
            raise MetaError("get_template needs a template_id")
        url = (f"https://graph.facebook.com/{C.GRAPH_API_VERSION}/{urllib.parse.quote(str(template_id).strip())}"
               f"?fields={','.join(fields)}")
        out = self.transport(method="GET", url=url, headers={"Authorization": "Bearer " + self.access_token},
                             data=None)
        if not isinstance(out, dict) or not out.get("name"):
            raise MetaError(f"Meta template lookup for {template_id} returned no template", payload=out)
        return ensure_approved(out) if require_approved else out

    def find_template(self, waba_id, name, language, require_approved=False, fields=TEMPLATE_FIELDS):
        """The one template with exactly this ``name`` and ``language`` on the WABA (names repeat
        across languages). None or several matches raise, naming the languages that do exist."""
        templates = self.list_message_templates(waba_id, fields=fields, name=name)
        same_name = [t for t in templates if t.get("name") == name]
        matches = [t for t in same_name if t.get("language") == language]
        if len(matches) != 1:
            raise MetaError(f"template {name!r} language {language!r}: {len(matches)} matches on WABA {waba_id} "
                            f"(languages with this name: {sorted({t.get('language') for t in same_name})})",
                            payload=matches or same_name)
        return ensure_approved(matches[0]) if require_approved else matches[0]

    def phone_number_info(self, fields=("display_phone_number", "verified_name")):
        """``GET /{phone_number_id}?fields=...``. The default is the set Graph v25.0 answers with this
        number's token. ``whatsapp_business_account`` is not in it: live 2026-09-14 it failed the whole call with
        ``(#100) Tried accessing nonexisting field``. The WABA id for ``list_message_templates``/
        ``find_template`` comes from ``META_WHATSAPP_WABA_ID`` (``C.WABA_ID``). Other fields: pass ``fields``."""
        if not self.access_token or not self.phone_number_id:
            raise MetaError("META_WHATSAPP_ACCESS_TOKEN / META_WHATSAPP_PHONE_NUMBER_ID are not set")
        url = f"https://graph.facebook.com/{C.GRAPH_API_VERSION}/{self.phone_number_id}?fields={','.join(fields)}"
        headers = {"Authorization": "Bearer " + self.access_token}
        return self.transport(method="GET", url=url, headers=headers, data=None)

    def list_message_templates(self, waba_id, limit=100, fields=None, name=None):
        """``GET /{waba_id}/message_templates`` -- every template already submitted for this
        WhatsApp Business Account (APPROVED/PENDING/REJECTED), paginated via ``paging.next``. A
        template approved here is approved for the account/phone number itself, not for whichever
        integration happened to submit it -- an APPROVED name found this way is immediately usable
        via ``send_template()``, no separate registration needed. ``fields`` and ``name`` are passed
        as Graph query parameters; ``name`` narrows server-side, callers still match exactly
        (``find_template``)."""
        if not self.access_token:
            raise MetaError("META_WHATSAPP_ACCESS_TOKEN is not set")
        headers = {"Authorization": "Bearer " + self.access_token}
        query = {"limit": limit}
        if fields:
            query["fields"] = ",".join(fields)
        if name:
            query["name"] = name
        url = (f"https://graph.facebook.com/{C.GRAPH_API_VERSION}/{waba_id}/message_templates?"
               + urllib.parse.urlencode(query, safe=","))
        templates = []
        while url:
            out = self.transport(method="GET", url=url, headers=headers, data=None)
            templates.extend(out.get("data") or [])
            url = (out.get("paging") or {}).get("next")
        return templates

    def media_url(self, media_id):
        """Step 1 of Meta's two-step media download: ``GET /{media-id}`` -> a JSON object with a
        temporary, token-gated CDN ``url`` (plus ``mime_type``/``sha256``/``file_size``/``id``).
        That URL expires quickly -- the caller must fetch it with ``download_media`` right away
        and never store it."""
        if not self.access_token:
            raise MetaError("META_WHATSAPP_ACCESS_TOKEN is not set")
        url = f"https://graph.facebook.com/{C.GRAPH_API_VERSION}/{media_id}"
        headers = {"Authorization": "Bearer " + self.access_token}
        out = self.transport(method="GET", url=url, headers=headers, data=None)
        if not isinstance(out, dict) or not out.get("url"):
            raise MetaError("Meta media lookup returned no url", payload=out)
        return out

    def download_media(self, url):
        """Step 2: fetch the temporary CDN ``url`` from ``media_url()``. Still needs the SAME
        bearer token -- the CDN link is not a public, unauthenticated URL despite looking like
        one. Returns raw bytes via ``media_transport`` (never the JSON-parsing ``transport``,
        which would corrupt binary content)."""
        if not self.access_token:
            raise MetaError("META_WHATSAPP_ACCESS_TOKEN is not set")
        headers = {"Authorization": "Bearer " + self.access_token}
        return self.media_transport(method="GET", url=url, headers=headers)
