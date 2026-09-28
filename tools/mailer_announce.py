"""The announcement PDF of a scheduled clinic-mailer batch (Ivan, 2026-09-28): one document with the whole plan.

clinic_mailer.plan builds it for a batch planned with --announce-at, and the batch's announcement mail carries it
to the operators. Pages:
  1. the plan: facts (first letters, every follow-up round with its report time), the campaign's own notes (config
     "announce.notes", plain text that clinic_mailer.notes_html turns into HTML), how to stop the mailing
  2. the schedule: every first letter to the minute, with To/Cc, greeting, subject, the letter's vacancies, the
     Kurzprofil version and the planned time of each follow-up, which goes by itself if the clinic does not answer
  3. the templates of every cadence step and the cadence rules, both read from the config
  4. every Kurzprofil version side by side: page 1 of each letter's PDF attachment, identical renderings shown once
  5. the first letter as the clinic sees it, with the signature image
Labels are Russian because the operators read Russian; the letters stay German. Nothing here sends or decides
anything: the caller passes the rows, this module only renders them (google-chrome, pdftoppm).
"""
import base64
import hashlib
import html
import io
import math
import re
import subprocess
from pathlib import Path

from PIL import Image, ImageOps

WD = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
KIND_RU = {"reply": "любой ответ клиники", "stop": "просьба больше не писать", "bounce": "недоставка (bounce)",
           "complaint": "жалоба на спам", "auto_reply": "автоответ", "unmatched": "непривязанное письмо с домена клиники"}
UNIT_RU = {"m": "мин.", "h": "ч.", "d": "календарных дн.", "bd": "рабочих дн."}
PLACEHOLDER = re.compile(r"(\[[A-ZÄÖÜ_/]+\])")

# A4 landscape inside the page margins, in mm; the versions page fits its grid into this box
WIDE_W, WIDE_H, GAP, CAPTION, TITLE = 277, 190, 6, 21, 16


def esc(s):
    return html.escape(str(s), quote=False)


def when(t):
    return f"{WD[t.weekday()]} {t:%d.%m} {t:%H:%M}"


def kurzprofil_versions(items, workdir):
    """(versions, label per recipient id, height/width of a thumbnail). A version is page 1 of an item's first PDF
    attachment, rendered at 150 dpi; items whose renderings are pixel-identical share one version. Every version is
    cropped to the same box, the union of their printed areas, so the marks show larger. Labels A, B, C ... in
    first-letter order."""
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    versions, by_key, label_of = [], {}, {}
    for it in items:
        pdf = next((a for a in it["attachments"] if a["name"].lower().endswith(".pdf")), None)
        if not pdf:
            continue
        stem = workdir / f"kp_{it['recipient_id']}"
        subprocess.run(["pdftoppm", "-png", "-r", "150", "-f", "1", "-l", "1", "-singlefile", pdf["path"], str(stem)],
                       check=True, capture_output=True)
        img = Image.open(stem.with_suffix(".png")).convert("RGB")
        key = hashlib.sha256(img.tobytes()).hexdigest()
        if key not in by_key:
            by_key[key] = {"label": chr(ord("A") + len(versions)), "img": img, "clinics": [], "marked": it.get("marked")}
            versions.append(by_key[key])
        by_key[key]["clinics"].append(it["clinic"])
        label_of[it["recipient_id"]] = by_key[key]["label"]
    if not versions:
        return versions, label_of, math.sqrt(2)
    boxes = [ImageOps.invert(v["img"].convert("L")).point(lambda p: 255 if p > 10 else 0).getbbox() for v in versions]
    w, h = versions[0]["img"].size
    pad = 14
    box = (max(min(b[0] for b in boxes) - pad, 0), max(min(b[1] for b in boxes) - pad, 0),
           min(max(b[2] for b in boxes) + pad, w), min(max(b[3] for b in boxes) + pad, h))
    for v in versions:
        buf = io.BytesIO()
        v.pop("img").crop(box).save(buf, "JPEG", quality=88)
        v["jpeg"] = buf.getvalue()
    return versions, label_of, (box[3] - box[1]) / (box[2] - box[0])


def grid_columns(n, ratio):
    """Columns that give n thumbnails of height/width `ratio` the largest width on one landscape page."""
    best = (0, 1)
    for cols in range(1, n + 1):
        rows = math.ceil(n / cols)
        w = min((WIDE_W - (cols - 1) * GAP) / cols, ((WIDE_H - TITLE - rows * CAPTION - (rows - 1) * GAP) / rows) / ratio)
        best = max(best, (w, cols))
    return best[1], best[0]


def shown_marks(phrases):
    """The marked phrases for a caption, without those inside a longer marked phrase (the mark shows the longer one)."""
    phrases = phrases or []
    return [p for p in phrases if not any(p != q and p in q for q in phrases)]


def template_block(path):
    return "<pre class='tpl'>" + PLACEHOLDER.sub(lambda m: f"<span class='ph'>{esc(m.group(1))}</span>", esc(Path(path).read_text().strip())) + "</pre>"


def cadence_rules(cfg, info):
    steps = cfg["cadence"]
    lines = [f"<li><b>{esc(steps[0]['step'])}</b>: по расписанию на стр. 2, время каждого письма в таблице.</li>"]
    for s in steps[1:]:
        n, unit = re.match(r"^(\d+)(m|h|d|bd)$", s["after"]).groups()
        keep = " в то же время суток" if unit in ("d", "bd") else ""
        lines.append(f"<li><b>{esc(s['step'])}</b>: через {n} {UNIT_RU[unit]} после предыдущего письма{keep}, "
                     + ("ответом в той же ветке (Re:)" if s.get("in_thread") else "новым письмом") + ".</li>")
    w = cfg["window"]
    days = "Пн–Пт" if w["weekdays"] == [1, 2, 3, 4, 5] else ", ".join(WD[d - 1] for d in w["weekdays"])
    hol = ", ".join(f"{d:%d.%m.%Y}" for d in sorted(cfg["holidays"])[:6]) + (" …" if len(cfg["holidays"]) > 6 else "")
    lo, hi = cfg["pause_seconds"]
    reports = [r["report"] for r in info["rounds"] if r["report"]]
    lines += [f"<li>Окно отправки: {days} {w['from']}–{w['to']} ({cfg['tz'].key}), кроме праздников: {esc(hol) or '—'}.</li>",
              f"<li>Между письмами случайная пауза {lo}–{hi} секунд; минута отправки никогда не кратна 5 (не 10:00, не 10:05).</li>",
              f"<li>Анонс уходит не позже чем за {info['window_minutes']} мин. до первого письма.</li>",
              "<li>Фоллоу-апы уходят сами в указанное в таблице время: одобрение этого плана покрывает и их.</li>"]
    if reports:
        lines.append(f"<li>В день каждого фоллоу-апа в {reports[0]:%H:%M}, за {info['window_minutes']} мин. до начала, операторам "
                     "уходит предварительный отчёт: кому уйдёт, кто ответил или убран, пример письма.</li>")
    lines += ["<li>Клиника выходит из последовательности, если пришло: " + ", ".join(KIND_RU.get(k, k) for k in cfg["stop_on"]) + ".</li>",
              "<li>Вся рассылка останавливается, если пришло: " + ", ".join(KIND_RU.get(k, k) for k in cfg["halt_on"]) + ".</li>",
              f"<li>Входящие ящика отправителя и команды читаются каждые {info['poll_seconds']} секунд, с анонса до конца рассылки.</li>"]
    return "<ul class='rules'>" + "".join(lines) + "</ul>"


def letter_html(item):
    """The item's HTML part, body only, with the inline signature image as a data URI."""
    body = re.search(r"(?s)<body>(.*)</body>", item["html"]).group(1)
    for img in item.get("inline", []):
        data = base64.b64encode(Path(img["path"]).read_bytes()).decode()
        ext = Path(img["path"]).suffix.lstrip(".").lower().replace("jpg", "jpeg")
        body = body.replace(f"cid:{img['cid']}", f"data:image/{ext};base64,{data}")
    return body


CSS = """
@page{size:A4;margin:13mm 13mm 12mm}
@page wide{size:A4 landscape;margin:10mm}
:root{--ink:#23201E;--ink-2:#5B5855;--ink-3:#8C8985;--accent:#202D85;--line:#E1E6E9;--surface:#F4F7F8;--warn:#9A3412}
*{box-sizing:border-box}
body{margin:0;background:#fff;color:var(--ink);font-family:"IBM Plex Sans","DejaVu Sans",sans-serif;font-size:10.5px;line-height:1.45;
  -webkit-print-color-adjust:exact;print-color-adjust:exact}
.ey{font-size:9px;letter-spacing:.18em;text-transform:uppercase;color:var(--ink-3);margin:0 0 3px}
h1{font-size:21px;line-height:1.15;margin:0 0 10px;font-weight:700}
h2{font-size:9.5px;letter-spacing:.16em;text-transform:uppercase;color:var(--accent);margin:16px 0 7px;border-top:1px solid var(--line);padding-top:8px;font-weight:700}
h2:first-child{margin-top:0;border-top:0;padding-top:0}
.facts{display:grid;grid-template-columns:150px 1fr;gap:3px 12px;margin:0}
.facts dt{font-size:9px;letter-spacing:.1em;text-transform:uppercase;color:var(--ink-3);padding-top:2px}
.facts dd{margin:0}
.box{border:1.5px solid var(--accent);border-radius:6px;padding:9px 12px;background:#F6F7FF}
.box p{margin:0 0 5px}.box ul{margin:4px 0 0;padding-left:16px}
.notes p{margin:0 0 6px}.notes ul{margin:0 0 6px;padding-left:16px}
.mono,.tpl,td.t{font-family:"JetBrains Mono","DejaVu Sans Mono",monospace}
.wide{page:wide;break-before:page}
.sheet{break-before:page}
table.plan{width:100%;border-collapse:collapse;font-size:8.6px;line-height:1.35}
table.plan th{text-align:left;font-size:8px;letter-spacing:.08em;text-transform:uppercase;color:var(--ink-3);border-bottom:1.5px solid var(--ink);padding:3px 5px}
table.plan td{border-bottom:1px solid var(--line);padding:4px 5px;vertical-align:top}
table.plan tr{break-inside:avoid}
td.t{font-weight:700;font-size:10px;white-space:nowrap}
td.n{color:var(--ink-3)}
.addr{font-family:"JetBrains Mono","DejaVu Sans Mono",monospace;font-size:8px;color:var(--ink-2);word-break:break-all}
.kp{display:inline-block;min-width:16px;text-align:center;font-weight:700;color:#fff;background:var(--accent);border-radius:3px;padding:0 4px}
.fu{white-space:nowrap;color:var(--ink-2)}
.tpl{white-space:pre-wrap;background:var(--surface);border:1px solid var(--line);border-radius:4px;padding:8px 10px;font-size:9px;line-height:1.5;margin:0 0 4px}
.ph{background:#E4E8FF;color:var(--accent);border-radius:3px;padding:0 2px}
.cols{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.cols h2{margin-top:10px}.cols>div>h2:first-child{margin-top:0;border-top:0;padding-top:0}
.rules{margin:0;padding-left:16px}.rules li{margin-bottom:3px}
.grid{display:grid;gap:6mm}
.v figure{margin:0}.v img{display:block;width:100%;border:1px solid var(--line)}
.v figcaption{font-size:7.6px;line-height:1.3;margin-top:3px;color:var(--ink-2)}
.v figcaption b{color:var(--ink)}
.mail{border:1px solid var(--line);border-radius:8px;overflow:hidden}
.mhead{background:var(--surface);border-bottom:1px solid var(--line);padding:9px 14px;display:grid;grid-template-columns:70px 1fr;gap:2px 10px;font-size:10px}
.mhead dt{color:var(--ink-3)}.mhead dd{margin:0}
.att{display:inline-block;border:1px solid var(--line);border-radius:4px;background:#fff;padding:1px 6px;font-size:9.5px}
.mbody{padding:14px 16px}
.small{font-size:9px;color:var(--ink-2)}
"""


def document(cfg, batch, rows, versions, label_of, ratio, notes_html, example, info):
    first, last, end = rows[0]["send_at"], rows[-1]["send_at"], info["end"]
    ops = ", ".join(batch["operators"])
    later = info["rounds"][1:]
    facts = ([("Кампания", esc(cfg["campaign"])), ("Отправитель", esc(f"{cfg['sender_name']} <{cfg['sender']}>")),
              ("Первые письма", f"{len(rows)}, {WD[first.weekday()]} {first:%d.%m.%Y}, с {first:%H:%M} до {last:%H:%M}")]
             + [(esc(r["name"].capitalize()), f"{WD[r['first'].weekday()]} {r['first']:%d.%m}, с {r['first']:%H:%M} до {r['last']:%H:%M}, "
                 f"кто не ответил; отчёт вам в {r['report']:%H:%M}") for r in later]
             + [("Команды", f"с письма-анонса до конца рассылки ({WD[end.weekday()]} {end:%d.%m}), от {esc(ops)}"),
                ("Пакет", f"<span class='mono'>{esc(batch['batch_id'])}</span>")])
    stop_box = (
        "<div class='box'><p><b>Как остановить.</b> Напишите на <span class='mono'>" + esc(cfg["sender"]) + "</span> "
        "(можно просто ответить на письмо-анонс) с любого из адресов: " + esc(ops) + ". Текст — обычными словами, "
        "на любом языке; письмо читает классификатор (Claude Haiku).</p><ul>"
        "<li><b>«стоп» / «отмена»</b> — до первого письма рассылка отменяется целиком, позже останавливается: не ушедшие "
        "письма и фоллоу-апы не уходят.</li>"
        "<li><b>«не отправлять в &lt;клинику&gt;»</b> — клиника убирается из рассылки вместе с её фоллоу-апами, остальные идут "
        "по плану.</li>"
        "<li><b>«статус»</b> — что ушло, что запланировано.</li></ul>"
        f"<p>Ящик читается каждые {info['poll_seconds']} секунд, с анонса до конца рассылки; ответ с результатом приходит вам "
        "обоим, обычно в течение пары минут. Другие просьбы (перенести время, поменять текст, продолжить после стопа) письмом "
        "не выполняются: ответ скажет, что нужен оператор. Письма, отправленные раньше анонса, не считаются командами."
        + (f" В день каждого фоллоу-апа в {later[0]['report']:%H:%M} приходит предварительный отчёт: кому уйдёт, кто ответил "
           "или убран." if later else "")
        + " Если классификатор не смог прочитать письмо или случилась ошибка, рассылка прерывается и вам приходит "
        "уведомление. После последнего письма приходит «рассылка завершена», и команды больше не принимаются.</p></div>")
    table = ["<table class='plan'><thead><tr><th>№</th><th>Время</th><th>Клиника</th><th>Кому / копия</th><th>Обращение и тема</th>"
             "<th>Вакансии в письме</th><th>KP</th><th>FU (если нет ответа)</th></tr></thead><tbody>"]
    for r in rows:
        table.append(
            f"<tr><td class='n'>{r['n']}</td><td class='t'>{r['send_at']:%H:%M}</td><td><b>{esc(r['clinic'])}</b></td>"
            f"<td><div class='addr'>{'<br>'.join(esc(a) for a in r['to'])}</div>"
            + (f"<div class='addr'>cc: {'<br>cc: '.join(esc(a) for a in r['cc'])}</div>" if r["cc"] else "")
            + f"</td><td>{esc(r['greeting'])}<div class='small'>{esc(r['subject'])}</div></td>"
            f"<td>{'<br>'.join('– ' + esc(a) for a in r['ads']) or '—'}</td>"
            f"<td>{'<span class=kp>' + label_of[r['recipient_id']] + '</span>' if r['recipient_id'] in label_of else '—'}</td>"
            f"<td class='fu'>{'<br>'.join(esc(s) + ' ' + when(t) for s, t in r['follow_ups'])}</td></tr>")
    table.append("</tbody></table>")
    tpl = [f"<h2>Шаблон · {esc(s['step'])}</h2>{template_block(s['template'])}" for s in cfg["cadence"]]
    sig = cfg.get("signature")
    sig_note = (f"<p class='small'>Под каждым письмом подпись: картинка <span class='mono'>{esc(Path(sig['image']).name)}</span> "
                f"в HTML-версии, в текстовой версии тот же текст:</p><pre class='tpl'>{esc(sig['text'])}</pre>") if sig else ""
    cols, w = grid_columns(max(len(versions), 1), ratio)
    grid = "".join(
        f"<div class='v'><figure><img src='data:image/jpeg;base64,{base64.b64encode(v['jpeg']).decode()}'>"
        f"<figcaption><b>Версия {v['label']}</b> · {esc(', '.join(v['clinics']))}"
        + (f"<br>Отмечено: {esc(' · '.join(shown_marks(v['marked'])))}" if v.get("marked") else "<br>Отмечено: ничего")
        + "</figcaption></figure></div>" for v in versions)
    ex = example
    mail = (f"<div class='mail'><dl class='mhead'><dt>От</dt><dd>{esc(cfg['sender_name'])} &lt;{esc(cfg['sender'])}&gt;</dd>"
            f"<dt>Кому</dt><dd>{esc(', '.join(ex['to']))}</dd>" + (f"<dt>Копия</dt><dd>{esc(', '.join(ex['cc']))}</dd>" if ex["cc"] else "")
            + f"<dt>Тема</dt><dd><b>{esc(ex['subject'])}</b></dd>"
            + (f"<dt>Вложение</dt><dd>{''.join('<span class=att>' + esc(a['name']) + '</span> ' for a in ex['attachments'])}</dd>" if ex["attachments"] else "")
            + f"<dt>Уходит</dt><dd>{when(ex['send_at'])}</dd></dl><div class='mbody'>{letter_html(ex)}</div></div>")
    return (
        "<!doctype html><html lang='ru'><head><meta charset='utf-8'><title>План рассылки</title>"
        "<link href='https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;600;700&family=JetBrains+Mono:wght@400;700&display=swap' rel='stylesheet'>"
        f"<style>{CSS}</style></head><body>"
        f"<p class='ey'>Анонс рассылки · {esc(cfg['campaign'])}</p><h1>Окончательный план: {len(rows)} клиник, первые письма "
        f"{WD[first.weekday()]} {first:%d.%m.%Y}, {first:%H:%M}–{last:%H:%M}</h1>"
        "<h2>Коротко</h2><dl class='facts'>" + "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in facts) + "</dl>"
        + (f"<h2>Что изменилось и почему</h2><div class='notes'>{notes_html}</div>" if notes_html else "")
        + f"<h2>Остановить или отменить</h2>{stop_box}"
        f"<section class='wide'><p class='ey'>Стр. 2 · расписание</p><h1>Расписание до минуты</h1>{''.join(table)}"
        "<p class='small'>KP — версия Kurzprofil (стр. 4). FU — фоллоу-ап в той же переписке: уходит сам в указанное время, "
        "если клиника не ответила; одобрение этого плана покрывает и его.</p></section>"
        f"<section class='sheet'><p class='ey'>Стр. 3 · шаблоны и каденция</p><h1>Шаблоны писем</h1>"
        "<p class='small'>Один шаблон на все клиники; <span class='ph'>[ПОЛЯ]</span> заполняются по клинике (значения — в "
        "таблице на стр. 2 и в примере на стр. 5).</p>"
        f"<div class='cols'><div>{tpl[0]}{sig_note}</div><div>{''.join(tpl[1:])}</div></div>"
        f"<h2>Каденция</h2>{cadence_rules(cfg, info)}</section>"
        f"<section class='wide'><p class='ey'>Стр. 4 · Kurzprofil</p><h1>Все версии анонимного профиля</h1>"
        f"<div class='grid' style='grid-template-columns:repeat({cols},{w:.1f}mm)'>{grid}</div></section>"
        f"<section class='sheet'><p class='ey'>Стр. 5 · пример</p><h1>Первое письмо так, как его увидит клиника</h1>{mail}</section>"
        "</body></html>")


def render_pdf(cfg, batch, rows, notes_html, example, info, pdf_path):
    """Write the announcement PDF (and its HTML next to it); return (PDF path, Kurzprofil version per recipient id).
    `info`: window_minutes, poll_seconds, rounds (clinic_mailer.rounds_info) and end (the last letter's time)."""
    pdf_path = Path(pdf_path).resolve()
    work = pdf_path.parent / (pdf_path.stem + "_parts")
    versions, label_of, ratio = kurzprofil_versions(rows, work)
    html_path = pdf_path.with_suffix(".html")
    html_path.write_text(document(cfg, batch, rows, versions, label_of, ratio, notes_html, example, info))
    subprocess.run(["google-chrome", "--headless=new", "--no-sandbox", "--disable-gpu", "--no-pdf-header-footer",
                    "--virtual-time-budget=8000", f"--print-to-pdf={pdf_path}", html_path.as_uri()], check=True, capture_output=True)
    return pdf_path, label_of
