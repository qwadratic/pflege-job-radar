"""A candidate's status page: one self-contained HTML document from one JSON object (TASK-434, TASK-436).

    python tools/status_page.py DATA.json > index.html

The page is what a candidate opens from the link in her WhatsApp chat (served at /s/{token}/, docs/auth.md), so it is
written for a phone first and in the board's light design (web/index.template.html: the same colours, type and square
corners). It answers, in this order: where her profile is and how each clinic stands, what she asked for, which
further clinics fit, and what the clinics see of her. Everything else she asks in the chat.

No script, no image, no request to this server: the only outside reference is the two webfonts, with a system
fallback. A filled page is never committed: it holds one person's wishes. This module and the invented example in
tests/fixtures/status_page/ are all the repository keeps.

DATA (every text is shown as given, escaped; German, formal address):
    as_of      "2026-10-06"                      the day the data was checked
    chat_url   optional link back to the chat (https://wa.me/...); without it the page only says "im WhatsApp-Chat"
    wishes     [{"label": "Station", "text": "PIC, sonst Intensiv"}]          what she asked for, most important first
    profile    [{"label": "Beruf", "value": "Pflegefachfrau"}]                what the clinics see, nothing else is shown
    sent       clinics that have her profile, the ones that moved first:
               {"name", "town", "travel", "state", "state_at", "note", "jobs": [..], "housing", "fit": {..}}
               state: one of HandoffStatus (app/wa/pro_models.py); any other value is shown as it is
    more       clinics that fit and do not have her profile yet: the same fields without state, plus
               "group": the heading they are listed under ("Ihre Wunschstation", "Andere Stationen")
    fit        {"station": "yes" | "part" | "no", "near": ..., "housing": ...}; a missing key shows no mark
"""
import html
import json
import sys
from datetime import date

# The state names are the harness's own closed list (app/wa/pro_models.py HandoffStatus), the ones the mail desk posts per
# clinic and Luna answers from: the page and the chat can then never name a clinic's state differently.
STATES = {"sent_to_clinic": ("Gesendet", "s-sent"), "followup_sent": ("Nachgefragt", "s-sent"),
          "clinic_replied": ("Antwort erhalten", "s-good"), "interview_scheduled": ("Gespräch", "s-good"),
          "trial_scheduled": ("Hospitation", "s-good"), "offer": ("Angebot", "s-good"), "contract_signed": ("Vertrag", "s-good"),
          "declined": ("Absage", "s-bad"), "closed": ("Abgeschlossen", "s-bad"), "halted": ("Pausiert", "")}
# What the headline counts as "a clinic has replied", the furthest step first: these cards are listed first, in this order.
MOVED = ("contract_signed", "offer", "trial_scheduled", "interview_scheduled", "clinic_replied")
FIT_KEYS = (("station", "Station"), ("near", "Nähe"), ("housing", "Wohnung"))
FIT_MARK = {"yes": ("✓", "passt"), "part": ("~", "teilweise"), "no": ("✗", "passt nicht")}

CSS = """
:root{--bg:#FFFFFF;--surface:#F4F7F8;--ink:#23201E;--ink-2:#5B5855;--ink-3:#8C8985;--accent:#202D85;--sky:#C8F1FF;--neon:#9DE146;--red:#D82434;--line:#E1E6E9}
*{box-sizing:border-box;border-radius:0}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);font-family:"JetBrains Mono",ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:15px;line-height:1.55;-webkit-font-smoothing:antialiased}
h1,h2,.brand b,.num b{font-family:"Archivo Black","Arial Black",system-ui,sans-serif;font-weight:400;letter-spacing:-.01em;line-height:1.12;margin:0}
a{color:var(--accent);text-underline-offset:3px}
.wrap{max-width:920px;margin:0 auto;padding:0 18px}
.top{border-bottom:1px solid var(--line)} .top .wrap{display:flex;align-items:baseline;justify-content:space-between;gap:12px;padding-top:14px;padding-bottom:12px}
.brand{line-height:1.1} .brand b{font-size:15px;display:block} .brand span,.eyebrow{font-size:10.5px;letter-spacing:.16em;text-transform:uppercase;color:var(--ink-3)}
.asof{font-size:12px;color:var(--ink-3);white-space:nowrap}
.hero{padding:30px 0 6px} h1{font-size:clamp(27px,7.4vw,46px);text-wrap:balance;max-width:18ch} h1 em{font-style:normal;display:inline-block;box-shadow:inset 0 -.14em 0 var(--neon)}
.lead{color:var(--ink-2);margin:14px 0 0;max-width:62ch}
.nums{display:grid;grid-template-columns:repeat(auto-fit,minmax(96px,1fr));gap:1px;background:var(--line);border:1px solid var(--line);margin:22px 0 0}
.num{background:var(--bg);padding:12px 12px 11px} .num b{display:block;font-size:26px;font-variant-numeric:tabular-nums} .num span{display:block;font-size:11.5px;color:var(--ink-2);line-height:1.3;margin-top:3px}
.num.hot{background:var(--neon)} .num.hot span{color:var(--ink)}
section{padding:34px 0 0} h2{font-size:clamp(20px,5.2vw,26px)} .sub{color:var(--ink-2);margin:6px 0 16px;font-size:13.5px;max-width:62ch}
.wishes{list-style:none;margin:14px 0 0;padding:0;border-top:1px solid var(--line)}
.wishes li{display:grid;grid-template-columns:86px minmax(0,1fr);gap:12px;padding:11px 0;border-bottom:1px solid var(--line)} .wishes .k{font-size:11px;letter-spacing:.12em;text-transform:uppercase;color:var(--ink-3);padding-top:3px}
.legend{display:flex;flex-wrap:wrap;gap:6px 14px;font-size:12px;color:var(--ink-2);margin:0 0 14px} .legend span{white-space:nowrap}
.cards{display:grid;gap:12px;grid-template-columns:minmax(0,1fr)}
.card{border:1px solid var(--line);padding:14px 14px 13px;min-width:0;break-inside:avoid} .card.moved{border-color:var(--ink);box-shadow:inset 4px 0 0 var(--neon)}
.card .hd{display:flex;flex-wrap:wrap;align-items:baseline;justify-content:space-between;gap:4px 12px}
.card h3{font:500 15.5px/1.3 inherit;font-family:inherit;margin:0;overflow-wrap:anywhere} .card .where{color:var(--ink-2);font-size:12.5px;margin:2px 0 0}
.st{font-size:11px;letter-spacing:.08em;text-transform:uppercase;padding:2px 7px;white-space:nowrap;border:1px solid var(--line);color:var(--ink-2)}
.st.s-sent{border-color:var(--accent);color:var(--accent)} .st.s-good{background:var(--neon);border-color:var(--neon);color:var(--ink)} .st.s-bad{border-color:var(--red);color:var(--red)}
.card .note{margin:9px 0 0;font-size:13.5px} .card.moved .note{font-weight:500}
.fit{display:flex;flex-wrap:wrap;gap:6px;margin:11px 0 0;padding:0;list-style:none} .fit li{font-size:12px;padding:2px 8px 2px 6px;white-space:nowrap;border:1px solid var(--line);color:var(--ink-3)}
.fit li b{font-weight:500;margin-right:5px} .fit .yes{background:var(--neon);border-color:var(--neon);color:var(--ink)} .fit .part{background:var(--sky);border-color:var(--sky);color:var(--ink)} .fit .no b{color:var(--red)}
.facts{margin:11px 0 0;font-size:13px;color:var(--ink-2)} .facts div{display:grid;grid-template-columns:76px minmax(0,1fr);gap:10px;padding:3px 0} .facts dt{color:var(--ink-3);font-size:11px;letter-spacing:.1em;text-transform:uppercase;padding-top:2px} .facts dd{margin:0;overflow-wrap:anywhere}
.group{font-size:11px;letter-spacing:.16em;text-transform:uppercase;color:var(--ink-3);margin:22px 0 10px} .group:first-of-type{margin-top:0} .group b{color:var(--ink);font-weight:500;margin-left:6px}
.profile{border:1px solid var(--line);background:var(--surface);padding:4px 14px;margin:14px 0 0} .profile div{display:grid;grid-template-columns:118px minmax(0,1fr);gap:12px;padding:9px 0;border-bottom:1px solid var(--line)} .profile div:last-child{border-bottom:0}
.profile dt{color:var(--ink-3);font-size:11px;letter-spacing:.1em;text-transform:uppercase;padding-top:3px} .profile dd{margin:0;overflow-wrap:anywhere}
.ask{margin:38px 0 0;background:var(--accent);color:#fff;padding:22px 18px} .ask h2{color:#fff} .ask p{margin:8px 0 0;max-width:56ch} .ask a{display:inline-block;margin-top:14px;background:var(--neon);color:var(--ink);padding:9px 16px;text-decoration:none;font-weight:500}
footer{color:var(--ink-3);font-size:12px;padding:20px 0 44px;max-width:70ch} .empty{color:var(--ink-2);border:1px dashed var(--line);padding:14px}
@media(min-width:760px){.wrap{padding:0 28px} .cards{grid-template-columns:repeat(2,minmax(0,1fr))} .hero{padding-top:46px} .ask{padding:28px}}
@media(max-width:360px){.wishes li,.profile div{grid-template-columns:minmax(0,1fr);gap:0}}
@media print{.ask a{display:none} body{font-size:10.5pt} .cards{grid-template-columns:repeat(2,minmax(0,1fr))} section{padding-top:18pt}}
"""


def e(text):
    return html.escape(str(text), quote=True)


def _day(iso):
    d = date.fromisoformat(iso)
    return f"{d.day:02d}.{d.month:02d}.{d.year}"


def _n(n, one, many):
    return f"{n} {one if n == 1 else many}"


def _card(c, with_state):
    state = c.get("state")
    label, cls = STATES.get(state, (state, "")) if with_state else (None, "")
    moved = with_state and state in MOVED
    out = [f'<article class="card{" moved" if moved else ""}"><div class="hd"><div><h3>{e(c["name"])}</h3>']
    where = " · ".join(x for x in (c.get("town"), c.get("travel")) if x)
    if where:
        out.append(f'<p class="where">{e(where)}</p>')
    out.append("</div>")
    if with_state:
        when = f' · {_day(c["state_at"])[:6]}' if c.get("state_at") else ""
        out.append(f'<span class="st {cls}">{e(label)}{when}</span>')
    out.append("</div>")
    if c.get("note"):
        out.append(f'<p class="note">{e(c["note"])}</p>')
    fit = c.get("fit") or {}
    marks = [(k, lab, fit[k]) for k, lab in FIT_KEYS if k in fit]
    if marks:
        out.append('<ul class="fit">' + "".join(
            f'<li class="{e(v)}"><b aria-hidden="true">{FIT_MARK.get(v, (e(v), ""))[0]}</b>{lab}<span class="vh">: {FIT_MARK.get(v, ("", e(v)))[1]}</span></li>'
            for k, lab, v in marks) + "</ul>")
    facts = [("Stellen", " · ".join(c["jobs"]))] if c.get("jobs") else []
    if c.get("housing"):
        facts.append(("Wohnung", c["housing"]))
    if facts:
        out.append('<dl class="facts">' + "".join(f"<div><dt>{k}</dt><dd>{e(v)}</dd></div>" for k, v in facts) + "</dl>")
    out.append("</article>")
    return "".join(out)


def render(data):
    sent, more = data.get("sent") or [], data.get("more") or []
    moved = [c for c in sent if c.get("state") in MOVED]
    order = {s: i for i, s in enumerate(MOVED)}
    sent = sorted(sent, key=lambda c: order.get(c.get("state"), len(order)))          # stable: the given order within a state
    if moved:
        h1 = f'Ihr Profil liegt bei {_n(len(sent), "Klinik", "Kliniken")}. <em>{_n(len(moved), "hat", "haben")} geantwortet.</em>'
    elif sent:
        h1 = f'Ihr Profil liegt bei <em>{_n(len(sent), "Klinik", "Kliniken")}</em>.'
    else:
        h1 = "Ihr Profil ist bereit. Wir haben es noch <em>nicht verschickt</em>."
    lead = ("Hier sehen Sie, wohin wir Ihr Profil geschickt haben und wie es dort steht. " if sent else "") + \
           ("Darunter stehen weitere Kliniken, die zu Ihren Wünschen passen." if more else "")
    nums = [(len(sent), "Kliniken haben Ihr Profil", False), (len(moved), "haben geantwortet", bool(moved)),
            (len(more), "weitere passen", False)]
    p = [f'<!doctype html><html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
         f'<meta name="robots" content="noindex, nofollow"><meta name="color-scheme" content="light"><title>Ihr Stand · Pflege-Stellen Bayern</title>'
         f'<link rel="preconnect" href="https://fonts.googleapis.com"><link href="https://fonts.googleapis.com/css2?family=Archivo+Black&amp;family=JetBrains+Mono:wght@400;500&amp;display=swap" rel="stylesheet">'
         f'<style>{CSS}.vh{{position:absolute;width:1px;height:1px;margin:-1px;overflow:hidden;clip-path:inset(50%);white-space:nowrap}}</style></head><body>',
         f'<header class="top"><div class="wrap"><div class="brand"><b>Pflege-Stellen</b><span>Bayern</span></div><div class="asof">Stand {_day(data["as_of"])}</div></div></header>',
         f'<main class="wrap"><div class="hero"><p class="eyebrow">Ihr Stand</p><h1>{h1}</h1>']
    if lead:
        p.append(f'<p class="lead">{lead}</p>')
    p.append('<div class="nums">' + "".join(f'<div class="num{" hot" if hot else ""}"><b>{n}</b><span>{lab}</span></div>' for n, lab, hot in nums) + "</div></div>")

    if data.get("wishes"):
        p.append('<section><h2>Das ist Ihnen wichtig</h2><ul class="wishes">' + "".join(
            f'<li><span class="k">{e(w["label"])}</span><span>{e(w["text"])}</span></li>' for w in data["wishes"]) + "</ul></section>")

    legend = ('<p class="legend"><span><b>✓</b> passt</span><span><b>~</b> teilweise</span><span><b>✗</b> passt nicht</span></p>')
    p.append('<section><h2>Hier liegt Ihr Profil</h2>')
    if sent:
        p.append('<p class="sub">Kliniken mit einer Antwort stehen oben. Die Zeichen zeigen, wie die Klinik zu Ihren Wünschen passt.</p>' + legend)
        p.append('<div class="cards">' + "".join(_card(c, True) for c in sent) + "</div>")
    else:
        p.append('<p class="empty">Noch bei keiner Klinik. Wir melden uns im Chat, sobald wir es verschicken.</p>')
    p.append("</section>")

    if more:
        p.append('<section><h2>Weitere Kliniken, die passen</h2><p class="sub">Diese Kliniken haben Ihr Profil noch nicht. '
                 'Schreiben Sie uns im Chat, wenn wir es dorthin schicken sollen.</p>' + (legend if not sent else ""))
        groups = []
        for c in more:
            g = c.get("group") or ""
            if not groups or groups[-1][0] != g:
                groups.append((g, []))
            groups[-1][1].append(c)
        for g, items in groups:
            if g:
                p.append(f'<p class="group">{e(g)}<b>{len(items)}</b></p>')
            p.append('<div class="cards">' + "".join(_card(c, False) for c in items) + "</div>")
        p.append("</section>")

    if data.get("profile"):
        p.append('<section><h2>Das sehen die Kliniken von Ihnen</h2><p class="sub">Ohne Ihren Namen und ohne Ihre Telefonnummer. '
                 'Die bekommt eine Klinik erst, wenn Sie zustimmen.</p><dl class="profile">' + "".join(
                     f'<div><dt>{e(f["label"])}</dt><dd>{e(f["value"])}</dd></div>' for f in data["profile"]) + "</dl></section>")

    p.append('<div class="ask"><h2>Fragen? Schreiben Sie uns.</h2><p>Alles Weitere beantworten wir im WhatsApp-Chat: Details zu einer Klinik, '
             'eine Änderung an Ihren Wünschen oder der nächste Schritt.</p>')
    if data.get("chat_url"):
        p.append(f'<a href="{e(data["chat_url"])}">Zum WhatsApp-Chat</a>')
    p.append(f'</div><footer>Stand {_day(data["as_of"])}. Stellen und Wohnungen: Angaben der Kliniken, sie ändern sich schnell. '
             f'Eine Antwort der Klinik ist nicht sicher. Dieser Link ist nur für Sie: Wer ihn hat, kann diese Seite lesen.</footer></main></body></html>')
    return "".join(p)


if __name__ == "__main__":
    sys.stdout.write(render(json.load(open(sys.argv[1], encoding="utf-8"))))
