"""Template lookup, send parameters and rendering (TASK-98, app/wa/meta.py). No network: Meta is a fake
transport that records calls. Synthetic templates, personas and numbers only."""
import json

import pytest

from app.wa import config as C
from app.wa import meta as M

LEAD = "+491700000098"


class FakeMeta:
    """Records every call. GET answers from ``gets`` (url substring -> response or list of pages);
    POST answers a wamid."""

    def __init__(self, gets=None):
        self.calls = []
        self.gets = gets or {}

    def __call__(self, method, url, headers=None, data=None, timeout=None):
        self.calls.append({"method": method, "url": url, "headers": headers,
                           "body": json.loads(data) if data else None})
        if method == "POST":
            return {"messages": [{"id": f"wamid.t98.{len(self.calls)}"}]}
        for key, answer in self.gets.items():
            if key in url:
                return answer.pop(0) if isinstance(answer, list) else answer
        raise AssertionError(f"unexpected GET {url}")

    @property
    def posts(self):
        return [c for c in self.calls if c["method"] == "POST"]


def _client(fake):
    return M.Client(transport=fake, access_token="tok", phone_number_id="pn1")


def _tpl(components, fmt="POSITIONAL", name="synthetic_tpl", language="de", status="APPROVED"):
    return {"id": "9800001", "name": name, "language": language, "status": status, "category": "MARKETING",
            "parameter_format": fmt, "components": components}


# The shape of the live campaign candidate (read back 2026-09-14), wording replaced.
CAMPAIGN = _tpl([
    {"type": "HEADER", "format": "TEXT", "text": "Neue Stellen in Bayern"},
    {"type": "BODY", "text": "Guten Tag {{1}}, Sie hatten sich bei uns gemeldet.\n\nIst das interessant?",
     "example": {"body_text": [["Frau Muster"]]}},
    {"type": "BUTTONS", "buttons": [{"type": "QUICK_REPLY", "text": "Ja, gerne"},
                                    {"type": "QUICK_REPLY", "text": "Nein, danke"}]},
])


# --- lookup ------------------------------------------------------------------------------------

def test_get_template_is_a_read_only_get_with_the_template_fields():
    fake = FakeMeta({"/9800001?": dict(CAMPAIGN)})
    out = _client(fake).get_template("9800001")
    assert out["name"] == "synthetic_tpl"
    (call,) = fake.calls
    assert call["method"] == "GET" and call["body"] is None
    assert call["url"] == (f"https://graph.facebook.com/{C.GRAPH_API_VERSION}/9800001"
                           "?fields=id,name,language,status,category,sub_category,parameter_format,components")
    assert call["headers"] == {"Authorization": "Bearer tok"}


def test_get_template_require_approved_names_the_status():
    fake = FakeMeta({"/9800001?": _tpl([], status="PENDING")})
    with pytest.raises(M.MetaError, match="is PENDING, not APPROVED"):
        _client(fake).get_template("9800001", require_approved=True)
    assert _client(FakeMeta({"/9800001?": _tpl([], status="PAUSED")})).get_template("9800001")["status"] == "PAUSED"
    assert _client(FakeMeta({"/9800001?": dict(CAMPAIGN)})).get_template("9800001", require_approved=True)


def test_get_template_fails_loudly_without_token_id_or_template():
    with pytest.raises(M.MetaError, match="ACCESS_TOKEN"):
        M.Client(transport=FakeMeta(), access_token="", phone_number_id="pn1").get_template("1")
    with pytest.raises(M.MetaError, match="needs a template_id"):
        _client(FakeMeta()).get_template(" ")
    with pytest.raises(M.MetaError, match="returned no template"):
        _client(FakeMeta({"/42?": {"id": "42"}})).get_template("42")


def test_find_template_matches_name_and_language_exactly_across_pages():
    pages = [
        {"data": [_tpl([], name="campaign_bayern_v2"), _tpl([], name="campaign_bayern", language="en")],
         "paging": {"next": "https://graph.facebook.com/next-page-2"}},
        {"data": [dict(_tpl([], name="campaign_bayern"), id="9800002")]},
    ]
    fake = FakeMeta({"message_templates?": [pages[0]], "next-page-2": [pages[1]]})
    out = _client(fake).find_template("waba-1", "campaign_bayern", "de", require_approved=True)
    assert out["id"] == "9800002"
    first = fake.calls[0]["url"]
    assert "/waba-1/message_templates?" in first
    assert "name=campaign_bayern" in first
    assert "fields=id,name,language,status,category,sub_category,parameter_format,components" in first
    assert all(c["method"] == "GET" for c in fake.calls)


def test_find_template_none_or_several_matches_raise_with_existing_languages():
    fake = FakeMeta({"message_templates?": {"data": [_tpl([], name="campaign_bayern", language="en")]}})
    with pytest.raises(M.MetaError, match=r"0 matches.*\['en'\]"):
        _client(fake).find_template("waba-1", "campaign_bayern", "de")
    twice = {"data": [_tpl([], name="campaign_bayern"), dict(_tpl([], name="campaign_bayern"), id="2")]}
    with pytest.raises(M.MetaError, match="2 matches"):
        _client(FakeMeta({"message_templates?": twice})).find_template("waba-1", "campaign_bayern", "de")


def test_list_message_templates_default_url_is_unchanged():
    fake = FakeMeta({"message_templates": {"data": []}})
    _client(fake).list_message_templates("waba-1")
    assert fake.calls[0]["url"] == f"https://graph.facebook.com/{C.GRAPH_API_VERSION}/waba-1/message_templates?limit=100"


# --- body: positional and named -------------------------------------------------------------------

def test_positional_body_from_list_or_mapping_in_placeholder_order():
    tpl = _tpl([{"type": "BODY", "text": "Hallo {{1}}, {{2}} Stellen in {{3}}."}])
    expected = [{"type": "body", "parameters": [{"type": "text", "text": "Ana"}, {"type": "text", "text": "12"},
                                               {"type": "text", "text": "Bayern"}]}]
    assert M.build_template_components(tpl, {"body": ["Ana", 12, "Bayern"]}) == expected
    assert M.build_template_components(tpl, {"body": {"3": "Bayern", 1: "Ana", "2": "12"}}) == expected
    assert M.render_template(tpl, {"body": ["Ana", 12, "Bayern"]})["body"] == "Hallo Ana, 12 Stellen in Bayern."


def test_named_body_carries_parameter_name_and_rejects_a_list():
    tpl = _tpl([{"type": "BODY", "text": "Hallo {{first_name}}, Region {{region}}."}], fmt="NAMED")
    out = M.build_template_components(tpl, {"body": {"region": "Bayern", "first_name": "Ana"}})
    assert out == [{"type": "body", "parameters": [
        {"type": "text", "parameter_name": "first_name", "text": "Ana"},
        {"type": "text", "parameter_name": "region", "text": "Bayern"}]}]
    assert M.validate_template_params(tpl, {"body": ["Ana", "Bayern"]}) == [
        "body: NAMED template needs a mapping {name: value}, got a list",
        "body: missing variable {{first_name}}", "body: missing variable {{region}}"]


def test_missing_and_extra_variables_are_all_named_before_any_post():
    tpl = _tpl([{"type": "HEADER", "format": "TEXT", "text": "Hallo {{first_name}}"},
                {"type": "BODY", "text": "{{first_name}}, {{city}}"}], fmt="NAMED")
    fake = FakeMeta()
    with pytest.raises(M.TemplateParamsError) as err:
        _client(fake).send_template(LEAD, definition=tpl,
                                    params={"header": {"firstname": "Ana"}, "body": {"first_name": "Ana", "zip": "1"}})
    assert err.value.problems == [
        "header: missing variable {{first_name}}", "header: extra variable {{firstname}} not in template",
        "body: missing variable {{city}}", "body: extra variable {{zip}} not in template"]
    assert "{{city}}" in str(err.value) and "{{zip}}" in str(err.value)
    assert fake.calls == []


def test_empty_invalid_and_typed_values():
    tpl = _tpl([{"type": "HEADER", "format": "TEXT", "text": "{{1}}"},
                {"type": "BODY", "text": "{{1}} {{2}} {{3}} {{4}}"}])
    problems = M.validate_template_params(tpl, {"header": [{"date_time": {"fallback_value": "Mo"}}],
                                                "body": [" ", None, {"currency": {"code": "EUR"}}, True]})
    assert problems == [
        "header {{1}}: unsupported value {'date_time': {'fallback_value': 'Mo'}} (text)",
        "body {{1}}: empty value", "body {{2}}: invalid value None",
        "body {{3}}: currency needs fallback_value, amount_1000", "body {{4}}: invalid value True"]
    params = {"header": ["Hallo"], "body": ["a", {"currency": {"fallback_value": "350 €", "code": "EUR",
                                                               "amount_1000": 350000}},
                                           {"date_time": {"fallback_value": "1. Oktober"}}, "d"]}
    body = M.build_template_components(tpl, params)[1]["parameters"]
    assert body[1] == {"type": "currency", "currency": {"fallback_value": "350 €", "code": "EUR", "amount_1000": 350000}}
    assert body[2] == {"type": "date_time", "date_time": {"fallback_value": "1. Oktober"}}
    assert M.render_template(tpl, params)["body"] == "a 350 € 1. Oktober d"


def test_parameter_format_must_be_known_when_variables_exist():
    assert M.validate_template_params(_tpl([{"type": "BODY", "text": "Hi {{1}}"}], fmt=None), {"body": ["x"]}) == [
        "definition parameter_format is missing, need NAMED or POSITIONAL"]
    assert M.validate_template_params(_tpl([{"type": "BODY", "text": "Hi"}], fmt=None), {}) == []
    assert M.validate_template_params(_tpl([{"type": "BODY", "text": "Hi {{name}}"}]), {"body": {"name": "x"}}) == [
        "body: placeholder {{name}} does not fit parameter_format POSITIONAL"]


def test_unknown_keys_values_for_absent_components_and_variable_free_text():
    tpl = _tpl([{"type": "BODY", "text": "Keine Variablen"}, {"type": "FOOTER", "text": "Fuss"}])
    assert M.validate_template_params(tpl, {"footer": "x", "header": ["x"], "body": ["x"], "buttons": {}}) == [
        "footer: unknown key (allowed: header, body, buttons, limited_time_offer, carousel, tap_target_configuration)",
        "body: template text has no variables, got ['x']",
        "header: template has no HEADER component", "buttons: template has no BUTTONS component"]
    assert M.validate_template_params(tpl, ["x"]) == [
        "params: expected a mapping of header, body, buttons, limited_time_offer, carousel, "
        "tap_target_configuration, got list"]
    with pytest.raises(M.MetaError, match="needs name and components"):
        M.validate_template_params({"name": "x"}, {})


# --- headers ------------------------------------------------------------------------------------

@pytest.mark.parametrize("fmt,supplied,param", [
    ("IMAGE", {"id": "media-1"}, {"type": "image", "image": {"id": "media-1"}}),
    ("VIDEO", {"link": "https://cdn.example/v.mp4"}, {"type": "video", "video": {"link": "https://cdn.example/v.mp4"}}),
    ("DOCUMENT", {"link": "https://cdn.example/a.pdf", "filename": "Stellen.pdf"},
     {"type": "document", "document": {"link": "https://cdn.example/a.pdf", "filename": "Stellen.pdf"}}),
])
def test_media_headers_by_id_or_link(fmt, supplied, param):
    tpl = _tpl([{"type": "HEADER", "format": fmt}, {"type": "BODY", "text": "Text"}])
    assert M.build_template_components(tpl, {"header": supplied}) == [{"type": "header", "parameters": [param]}]


def test_media_header_problems():
    tpl = _tpl([{"type": "HEADER", "format": "IMAGE"}, {"type": "BODY", "text": "Text"}])
    assert M.validate_template_params(tpl, {}) == ["header: missing IMAGE media (id or link)"]
    assert M.validate_template_params(tpl, {"header": {"id": "1", "link": "https://x"}}) == [
        "header: IMAGE media needs exactly one of id or link, got id and link"]
    assert M.validate_template_params(tpl, {"header": {"id": "1", "filename": "a.pdf"}}) == [
        "header: extra key 'filename' (allowed: id, link)"]
    gif = _tpl([{"type": "HEADER", "format": "GIF"}, {"type": "BODY", "text": "Text"}])
    assert M.validate_template_params(gif, {"header": {"id": "1"}}) == [
        "header: GIF headers send only through the Marketing Messages API, not Cloud API /messages"]


def test_location_header_needs_coordinates_name_and_address_optional():
    tpl = _tpl([{"type": "HEADER", "format": "LOCATION"}, {"type": "BODY", "text": "Hier"}])
    loc = {"latitude": "48.137", "longitude": "11.575", "name": "Klinik Muster", "address": "Musterstr. 1"}
    assert M.build_template_components(tpl, {"header": loc}) == [
        {"type": "header", "parameters": [{"type": "location", "location": loc}]}]
    assert M.render_template(tpl, {"header": loc})["text"] == "[location: Klinik Muster, Musterstr. 1 (48.137, 11.575)]\n\nHier"
    assert M.validate_template_params(tpl, {"header": {"longitude": "11.575", "zoom": 3}}) == [
        "header: extra key 'zoom' (allowed: address, latitude, longitude, name)", "header: missing latitude"]


def test_product_header_with_spm_button_takes_no_button_values():
    tpl = _tpl([{"type": "HEADER", "format": "PRODUCT"}, {"type": "BODY", "text": "Produkt"},
                {"type": "BUTTONS", "buttons": [{"type": "SPM", "text": "Ansehen"}]}])
    product = {"product_retailer_id": "sku-1", "catalog_id": "cat-1"}
    assert M.build_template_components(tpl, {"header": product}) == [
        {"type": "header", "parameters": [{"type": "product", "product": product}]}]
    assert M.validate_template_params(tpl, {"header": {"product_retailer_id": "sku-1"}, "buttons": {"0": {"x": 1}}}) == [
        "header: missing catalog_id", "buttons[0]: extra key 'x' (takes no values)"]


# --- buttons ------------------------------------------------------------------------------------

BUTTONS = _tpl([
    {"type": "BODY", "text": "Angebot"},
    {"type": "BUTTONS", "buttons": [
        {"type": "QUICK_REPLY", "text": "Ja"},
        {"type": "QUICK_REPLY", "text": "Nein"},
        {"type": "URL", "text": "Stellen", "url": "https://jobs.example/c/{{1}}", "example": ["abc"]},
        {"type": "URL", "text": "Info", "url": "https://jobs.example/info"},
        {"type": "PHONE_NUMBER", "text": "Anrufen", "phone_number": "+4989000000"},
        {"type": "COPY_CODE", "example": "CODE1"},
        {"type": "FLOW", "text": "Formular", "flow_id": "f1", "flow_action": "navigate"},
        {"type": "VOICE_CALL", "text": "WhatsApp-Anruf"},
    ]},
])


def test_every_button_value_builds_the_meta_shape():
    params = {"buttons": {"0": {"payload": "camp:yes"}, "1": {"payload": "camp:no"}, "2": ["token-7"],
                          "5": {"coupon_code": "BAY25"},
                          "6": {"flow_token": "ft-1", "flow_action_data": {"screen": "START"}},
                          "7": {"ttl_minutes": 1440, "payload": "call-camp"}}}
    assert M.build_template_components(BUTTONS, params) == [
        {"type": "button", "sub_type": "quick_reply", "index": 0, "parameters": [{"type": "payload", "payload": "camp:yes"}]},
        {"type": "button", "sub_type": "quick_reply", "index": 1, "parameters": [{"type": "payload", "payload": "camp:no"}]},
        {"type": "button", "sub_type": "url", "index": 2, "parameters": [{"type": "text", "text": "token-7"}]},
        {"type": "button", "sub_type": "copy_code", "index": 5, "parameters": [{"type": "coupon_code", "coupon_code": "BAY25"}]},
        {"type": "button", "sub_type": "flow", "index": 6, "parameters": [
            {"type": "action", "action": {"flow_token": "ft-1", "flow_action_data": {"screen": "START"}}}]},
        {"type": "button", "sub_type": "voice_call", "index": 7, "parameters": [
            {"type": "ttl_minutes", "ttl_minutes": 1440}, {"type": "payload", "payload": "call-camp"}]},
    ]
    view = M.render_template(BUTTONS, params)
    assert view["buttons"] == [
        {"type": "QUICK_REPLY", "text": "Ja", "payload": "camp:yes"},
        {"type": "QUICK_REPLY", "text": "Nein", "payload": "camp:no"},
        {"type": "URL", "text": "Stellen", "url": "https://jobs.example/c/token-7"},
        {"type": "URL", "text": "Info", "url": "https://jobs.example/info"},
        {"type": "PHONE_NUMBER", "text": "Anrufen", "phone_number": "+4989000000"},
        {"type": "COPY_CODE", "text": None, "code": "BAY25"},
        {"type": "FLOW", "text": "Formular"},
        {"type": "VOICE_CALL", "text": "WhatsApp-Anruf", "ttl_minutes": 1440, "payload": "call-camp"},
    ]
    assert view["text"] == ("Angebot\n\n[Ja]\n[Nein]\n[Stellen -> https://jobs.example/c/token-7]\n"
                            "[Info -> https://jobs.example/info]\n[Anrufen -> tel:+4989000000]\n[Copy code: BAY25]\n"
                            "[Formular]\n[WhatsApp-Anruf]")


def test_optional_button_values_can_be_omitted_required_ones_cannot():
    params = {"buttons": {"2": ["token-7"], "5": {"coupon_code": "BAY25"}}}
    out = M.build_template_components(BUTTONS, params)
    assert [(c["sub_type"], c["index"]) for c in out] == [("url", 2), ("copy_code", 5)]
    assert M.render_template(BUTTONS, params)["buttons"][0] == {"type": "QUICK_REPLY", "text": "Ja", "payload": None}
    assert M.validate_template_params(BUTTONS, {}) == ["buttons[2]: missing variable {{1}}", "buttons[5]: missing coupon_code"]


def test_button_problems_name_index_and_key():
    params = {"buttons": {"0": {"payload": "p", "text": "x"}, "2": ["a", "b"], "3": ["x"], "4": {"phone": "1"},
                          "5": {"coupon_code": "C"}, "9": {"payload": "p"}, "zero": {}, 0: {"payload": "dup"}}}
    assert M.validate_template_params(BUTTONS, params) == [
        "buttons[9]: template has no button at this index (8 buttons)",
        "buttons[zero]: template has no button at this index (8 buttons)",
        "buttons[0]: given twice",
        "buttons[0]: extra key 'text' (allowed: payload)",
        "buttons[2]: extra variable {{2}} not in template",
        "buttons[3]: template text has no variables, got ['x']",
        "buttons[4]: extra key 'phone' (takes no values)",
    ]
    assert M.validate_template_params(BUTTONS, {"buttons": [{"payload": "p"}]}) == [
        "buttons: expected a mapping {index: values}, got list", "buttons[2]: missing variable {{1}}",
        "buttons[5]: missing coupon_code"]


def test_named_url_button_otp_catalog_mpm_and_unsupported_type():
    tpl = _tpl([
        {"type": "BODY", "text": "{{code}} ist Ihr Code."},
        {"type": "BUTTONS", "buttons": [
            {"type": "OTP", "otp_type": "COPY_CODE", "text": "Code kopieren"},
            {"type": "URL", "text": "Bestellung", "url": "https://shop.example/o?name={{customer_name}}"},
            {"type": "CATALOG", "text": "Katalog"},
            {"type": "MPM", "text": "Auswahl"},
            {"type": "ORDER_DETAILS", "text": "Zahlen"},
        ]},
    ], fmt="NAMED")
    assert M.validate_template_params(tpl, {"body": {"code": "123456"}}) == [
        "buttons[0]: missing code", "buttons[1]: missing variable {{customer_name}}",
        "buttons[3]: missing sections", "buttons[3]: missing thumbnail_product_retailer_id",
        "buttons[4]: unsupported button type ORDER_DETAILS"]
    sections = [{"title": "Top", "product_items": [{"product_retailer_id": "sku-1"}]}]
    tpl["components"][1]["buttons"].pop()
    params = {"body": {"code": "123456"},
              "buttons": {"0": {"code": "123456"}, "1": {"customer_name": "Gon%C3%A7alves"},
                          "2": {"thumbnail_product_retailer_id": "sku-1"},
                          "3": {"thumbnail_product_retailer_id": "sku-1", "sections": sections}}}
    assert M.build_template_components(tpl, params)[1:] == [
        {"type": "button", "sub_type": "url", "index": 0, "parameters": [{"type": "text", "text": "123456"}]},
        {"type": "button", "sub_type": "url", "index": 1, "parameters": [
            {"type": "text", "parameter_name": "customer_name", "text": "Gon%C3%A7alves"}]},
        {"type": "button", "sub_type": "catalog", "index": 2, "parameters": [
            {"type": "action", "action": {"thumbnail_product_retailer_id": "sku-1"}}]},
        {"type": "button", "sub_type": "mpm", "index": 3, "parameters": [
            {"type": "action", "action": {"thumbnail_product_retailer_id": "sku-1", "sections": sections}}]},
    ]
    view = M.render_template(tpl, params)
    assert view["buttons"][0] == {"type": "OTP", "text": "Code kopieren", "code": "123456"}
    assert view["buttons"][1]["url"] == "https://shop.example/o?name=Gon%C3%A7alves"


# --- limited-time offer, carousel, call permission, tap target ------------------------------------

def test_limited_time_offer_needs_an_integer_expiration():
    tpl = _tpl([{"type": "HEADER", "format": "IMAGE"},
                {"type": "LIMITED_TIME_OFFER", "limited_time_offer": {"text": "Nur kurz!", "has_expiration": True}},
                {"type": "BODY", "text": "Code {{1}}"},
                {"type": "BUTTONS", "buttons": [{"type": "COPY_CODE", "example": "X"},
                                                {"type": "URL", "text": "Los", "url": "https://x.example/{{1}}"}]}])
    assert M.validate_template_params(tpl, {"header": {"id": "m1"}, "body": ["B25"],
                                            "buttons": {"0": {"coupon_code": "B25"}, "1": ["r"]}}) == [
        "limited_time_offer: missing expiration_time_ms"]
    assert M.validate_template_params(tpl, {"header": {"id": "m1"}, "body": ["B25"],
                                            "limited_time_offer": {"expiration_time_ms": "soon"},
                                            "buttons": {"0": {"coupon_code": "B25"}, "1": ["r"]}}) == [
        "limited_time_offer: expiration_time_ms must be an integer, got 'soon'"]
    params = {"header": {"id": "m1"}, "body": ["B25"], "limited_time_offer": {"expiration_time_ms": 1790000000000},
              "buttons": {"0": {"coupon_code": "B25"}, "1": ["r"]}}
    assert M.build_template_components(tpl, params)[1] == {"type": "limited_time_offer", "parameters": [
        {"type": "limited_time_offer", "limited_time_offer": {"expiration_time_ms": 1790000000000}}]}
    assert M.render_template(tpl, params)["text"] == (
        "[image: media id m1]\n\n[offer: Nur kurz!, expires 2026-09-21T14:13:20+00:00]\n\nCode B25\n\n"
        "[Copy code: B25]\n[Los -> https://x.example/r]")
    no_exp = _tpl([{"type": "LIMITED_TIME_OFFER", "limited_time_offer": {"text": "Aktion"}},
                   {"type": "BODY", "text": "Text"}])
    assert M.validate_template_params(no_exp, {"limited_time_offer": {"expiration_time_ms": 1}}) == [
        "limited_time_offer: extra key 'expiration_time_ms' (takes no values)"]


CAROUSEL = _tpl([
    {"type": "BODY", "text": "Hallo {{1}}, zwei Kliniken:"},
    {"type": "CAROUSEL", "cards": [
        {"components": [{"type": "HEADER", "format": "IMAGE"}, {"type": "BODY", "text": "Klinik {{1}}"},
                        {"type": "BUTTONS", "buttons": [{"type": "QUICK_REPLY", "text": "Mehr"},
                                                        {"type": "URL", "text": "Ansehen", "url": "https://k.example/{{1}}"}]}]}
        for _ in range(2)
    ]},
])


def test_carousel_cards_build_render_and_report_per_card():
    params = {"body": ["Ana"], "carousel": [
        {"header": {"id": "img-a"}, "body": ["A"], "buttons": {"0": {"payload": "card-a"}, "1": ["a"]}},
        {"header": {"link": "https://cdn.example/b.jpg"}, "body": ["B"], "buttons": {"1": ["b"]}},
    ]}
    out = M.build_template_components(CAROUSEL, params)
    assert out[0] == {"type": "body", "parameters": [{"type": "text", "text": "Ana"}]}
    assert out[1]["type"] == "carousel"
    assert out[1]["cards"][0] == {"card_index": 0, "components": [
        {"type": "header", "parameters": [{"type": "image", "image": {"id": "img-a"}}]},
        {"type": "body", "parameters": [{"type": "text", "text": "A"}]},
        {"type": "button", "sub_type": "quick_reply", "index": 0, "parameters": [{"type": "payload", "payload": "card-a"}]},
        {"type": "button", "sub_type": "url", "index": 1, "parameters": [{"type": "text", "text": "a"}]}]}
    assert out[1]["cards"][1]["card_index"] == 1
    view = M.render_template(CAROUSEL, params)
    assert view["carousel"][1] == {"header": {"format": "IMAGE", "image": {"link": "https://cdn.example/b.jpg"}},
                                   "body": "Klinik B",
                                   "buttons": [{"type": "QUICK_REPLY", "text": "Mehr", "payload": None},
                                               {"type": "URL", "text": "Ansehen", "url": "https://k.example/b"}]}
    assert view["text"] == ("Hallo Ana, zwei Kliniken:\n\n[card 1]\n[image: media id img-a]\n\nKlinik A\n\n[Mehr]\n"
                            "[Ansehen -> https://k.example/a]\n\n[card 2]\n[image: https://cdn.example/b.jpg]\n\n"
                            "Klinik B\n\n[Mehr]\n[Ansehen -> https://k.example/b]")
    assert M.validate_template_params(CAROUSEL, {"body": ["Ana"], "carousel": [
        {"header": {"id": "img-a"}, "body": ["A"], "buttons": {"1": ["a"]}, "footer": "x"}]}) == [
        "carousel: template has 2 cards, got 1", "carousel[0].footer: unknown key (allowed: header, body, buttons)",
        "carousel[1].header: missing IMAGE media (id or link)", "carousel[1].body: missing variable {{1}}",
        "carousel[1].buttons[1]: missing variable {{1}}"]


def test_call_permission_request_template_sends_body_only_and_renders_the_request():
    tpl = _tpl([{"type": "BODY", "text": "Dürfen wir Sie anrufen, {{1}}?"}, {"type": "CALL_PERMISSION_REQUEST"}])
    assert M.build_template_components(tpl, {"body": ["Ana"]}) == [
        {"type": "body", "parameters": [{"type": "text", "text": "Ana"}]}]
    view = M.render_template(tpl, {"body": ["Ana"]})
    assert view["call_permission_request"] is True
    assert view["text"] == "Dürfen wir Sie anrufen, Ana?\n\n[call permission request]"


def test_tap_target_configuration_is_send_only_and_validated():
    tpl = _tpl([{"type": "BODY", "text": "Neue Stellen"}])
    rows = [{"url": "https://jobs.example/", "title": "Stellen ansehen"}]
    assert M.build_template_components(tpl, {"tap_target_configuration": rows}) == [
        {"type": "tap_target_configuration", "parameters": [
            {"type": "tap_target_configuration", "tap_target_configuration": rows}]}]
    assert M.render_template(tpl, {"tap_target_configuration": rows})["text"] == (
        "Neue Stellen\n\n[Stellen ansehen -> https://jobs.example/]")
    assert M.validate_template_params(tpl, {"tap_target_configuration": [{"url": "https://jobs.example/"}]}) == [
        "tap_target_configuration[0]: missing title"]
    assert M.validate_template_params(tpl, {"tap_target_configuration": {}}) == [
        "tap_target_configuration: expected a non-empty list of {url, title}"]


def test_unsupported_component_type_fails_loudly():
    tpl = _tpl([{"type": "BODY", "text": "x"}, {"type": "ORDER_STATUS"}])
    with pytest.raises(M.TemplateParamsError, match="components: unsupported component type ORDER_STATUS"):
        M.render_template(tpl, {})


# --- rendering ----------------------------------------------------------------------------------

def test_render_is_exactly_what_the_recipient_sees():
    tpl = _tpl([
        {"type": "HEADER", "format": "DOCUMENT"},
        {"type": "BODY", "text": "Guten Tag {{1}},\n\n*{{2}}* neue Stellen."},
        {"type": "FOOTER", "text": "Antworten Sie STOP zum Abmelden"},
        {"type": "BUTTONS", "buttons": [{"type": "QUICK_REPLY", "text": "Ja"},
                                        {"type": "URL", "text": "Liste", "url": "https://jobs.example/{{1}}"}]},
    ])
    params = {"header": {"link": "https://cdn.example/l.pdf", "filename": "Liste.pdf"}, "body": ["Herr Beispiel", 7],
              "buttons": {"0": {"payload": "yes"}, "1": ["bayern"]}}
    view = M.render_template(tpl, params)
    assert view == {
        "name": "synthetic_tpl", "language": "de",
        "header": {"format": "DOCUMENT", "document": {"link": "https://cdn.example/l.pdf", "filename": "Liste.pdf"}},
        "limited_time_offer": None,
        "body": "Guten Tag Herr Beispiel,\n\n*7* neue Stellen.",
        "footer": "Antworten Sie STOP zum Abmelden",
        "buttons": [{"type": "QUICK_REPLY", "text": "Ja", "payload": "yes"},
                    {"type": "URL", "text": "Liste", "url": "https://jobs.example/bayern"}],
        "carousel": None, "call_permission_request": False, "tap_target_configuration": None,
        "text": ("[document: Liste.pdf https://cdn.example/l.pdf]\n\nGuten Tag Herr Beispiel,\n\n*7* neue Stellen.\n\n"
                 "Antworten Sie STOP zum Abmelden\n\n[Ja]\n[Liste -> https://jobs.example/bayern]"),
    }
    with pytest.raises(M.TemplateParamsError, match=r"body: missing variable \{\{2\}\}"):
        M.render_template(tpl, dict(params, body=["Herr Beispiel"]))


# --- send_template ------------------------------------------------------------------------------

def test_send_template_with_definition_posts_built_components_and_render_matches():
    fake = FakeMeta()
    params = {"body": ["Frau Muster"], "buttons": {"0": {"payload": "camp-98:yes"}, "1": {"payload": "camp-98:no"}}}
    wamid = _client(fake).send_template(LEAD, definition=CAMPAIGN, params=params)
    (post,) = fake.posts
    assert wamid == "wamid.t98.1"
    assert post["url"].endswith("/pn1/messages")
    assert post["body"] == {"messaging_product": "whatsapp", "to": "491700000098", "type": "template", "template": {
        "name": "synthetic_tpl", "language": {"code": "de"}, "components": [
            {"type": "body", "parameters": [{"type": "text", "text": "Frau Muster"}]},
            {"type": "button", "sub_type": "quick_reply", "index": 0, "parameters": [{"type": "payload", "payload": "camp-98:yes"}]},
            {"type": "button", "sub_type": "quick_reply", "index": 1, "parameters": [{"type": "payload", "payload": "camp-98:no"}]}]}}
    assert M.render_template(CAMPAIGN, params)["text"] == (
        "Neue Stellen in Bayern\n\nGuten Tag Frau Muster, Sie hatten sich bei uns gemeldet.\n\nIst das interessant?\n\n"
        "[Ja, gerne]\n[Nein, danke]")


def test_send_template_with_definition_checks_name_and_language_and_sends_nothing_on_error():
    fake = FakeMeta()
    cl = _client(fake)
    with pytest.raises(M.MetaError, match="language='en' does not match the definition's 'de'"):
        cl.send_template(LEAD, language="en", definition=CAMPAIGN, params={"body": ["x"]})
    with pytest.raises(M.MetaError, match="template_name='other' does not match"):
        cl.send_template(LEAD, "other", definition=CAMPAIGN, params={"body": ["x"]})
    with pytest.raises(M.TemplateParamsError, match=r"body: missing variable \{\{1\}\}"):
        cl.send_template(LEAD, "synthetic_tpl", "de", definition=CAMPAIGN, params={})
    assert fake.calls == []
    no_vars = _tpl([{"type": "BODY", "text": "Hallo"}])
    cl.send_template(LEAD, definition=no_vars)
    assert fake.posts[0]["body"]["template"] == {"name": "synthetic_tpl", "language": {"code": "de"}}


def test_send_template_legacy_signature_is_unchanged():
    fake = FakeMeta()
    cl = _client(fake)
    cl.send_template(LEAD, "candidate_reopen_v1")
    cl.send_template(LEAD, "candidate_reopen_v1", "en", ["Ionel", 3])
    cl.send_template(LEAD, "candidate_reopen_v1", "de", params=["Ionel"])
    assert [p["body"]["template"] for p in fake.posts] == [
        {"name": "candidate_reopen_v1", "language": {"code": "de"}},
        {"name": "candidate_reopen_v1", "language": {"code": "en"}, "components": [
            {"type": "body", "parameters": [{"type": "text", "text": "Ionel"}, {"type": "text", "text": "3"}]}]},
        {"name": "candidate_reopen_v1", "language": {"code": "de"}, "components": [
            {"type": "body", "parameters": [{"type": "text", "text": "Ionel"}]}]},
    ]
    with pytest.raises(M.MetaError, match="needs a template_name"):
        cl.send_template(LEAD)
    with pytest.raises(M.MetaError, match="structured template params need definition="):
        cl.send_template(LEAD, "candidate_reopen_v1", "de", {"body": ["x"]})
    assert len(fake.posts) == 3


def test_empty_supplied_values_are_reported_not_dropped():
    tpl = _tpl([{"type": "BODY", "text": "x"},
                {"type": "BUTTONS", "buttons": [{"type": "QUICK_REPLY", "text": "Ja"}, {"type": "MPM", "text": "Auswahl"},
                                                {"type": "FLOW", "text": "Formular"}]}])
    assert M.validate_template_params(tpl, {"buttons": {"0": {"payload": " "}, "1": {
        "thumbnail_product_retailer_id": "sku-1", "sections": []}, "2": {"flow_action_data": {}}}}) == [
        "buttons[0]: empty payload", "buttons[1]: empty sections", "buttons[2]: empty flow_action_data"]


def test_find_template_require_approved_and_definition_without_language():
    fake = FakeMeta({"message_templates?": {"data": [_tpl([], name="campaign_bayern", status="REJECTED")]}})
    with pytest.raises(M.MetaError, match="is REJECTED, not APPROVED"):
        _client(fake).find_template("waba-1", "campaign_bayern", "de", require_approved=True)
    no_lang = dict(_tpl([{"type": "BODY", "text": "Hallo"}]), language=None)
    with pytest.raises(M.MetaError, match="has no language"):
        _client(FakeMeta()).send_template(LEAD, definition=no_lang)
