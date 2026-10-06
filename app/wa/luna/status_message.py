"""The WhatsApp status message to a candidate whose profile went out to clinics (TASK-439).

Two bubbles. Every word is fixed text Ivan approved (prompts.STATUS_*_DE), every fact comes from her
status-page JSON (the file the email lane builds, tools/status_page.py renders and
tools/status_docs_publish.py publishes), and the link is appended here, never written by a model:

    Wir haben Ihr Profil an diese Klinik geschickt, sie passt besonders gut zu Ihren Wünschen:
    {name}, {town}
    Stellen: „{job}“; „{job}“
    Wohnung: {housing}
    Weg: {travel}

    Insgesamt ist Ihr anonymisiertes Profil an {N} Kliniken gegangen. Wir warten jetzt auf deren Antworten.
    Alle Kliniken und den vollständigen Bericht finden Sie hier:
    {url}

THE CLINIC is the one `sent` entry with `best: true`. The email lane picks it, so the message opens with the
same clinic her page lists first; this module never ranks. N is the number of `sent` entries: the clinics her
profile reached (the email lane already leaves out a clinic whose only answer was a bounce). Each job title is
quoted („…“) exactly as the posting has it, Ivan 2026-10-06: a title such as "Pflegefachkraft für unsere IMC" is
the clinic speaking, and the quotes keep it from reading as ours.

ANYTHING ELSE IS A LOUD ValueError, NEVER A GUESS (CLAUDE.md "no safety nets"): no best entry or more than one,
a best entry missing a field, fewer than two clinics (the approved wording is plural only, and a singular is
text Ivan has not approved), or a link that is not a status-page link. Nothing here sends anything.
"""
from __future__ import annotations

from app.wa import config as C
from app.wa import status_docs as SD
from app.wa.luna import prompts as P

#: The `sent` entry fields bubble 1 reads; each must be present and non-empty.
BEST_FIELDS = ("name", "town", "jobs", "housing", "travel")


def best_clinic(status):
    """-> the one `sent` entry marked `best: true`. ValueError when there is none or more than one."""
    best = [e for e in status["sent"] if e.get("best") is True]
    if len(best) != 1:
        names = [e.get("name") for e in best]
        raise ValueError(f"status JSON must mark exactly one sent clinic best: true, found {len(best)} {names}")
    entry = best[0]
    missing = [f for f in BEST_FIELDS if not entry.get(f)]
    if missing:
        raise ValueError(f"best clinic {entry.get('name')!r} is missing {missing}")
    jobs = entry["jobs"]
    if not isinstance(jobs, list) or not all(isinstance(j, str) and j.strip() for j in jobs):
        raise ValueError(f"best clinic {entry['name']!r}: jobs must be a list of non-empty strings, got {jobs!r}")
    return entry


def check_url(url):
    """The link must be a status-page link: the public base, a token TOKEN_RE accepts, a trailing "/"."""
    base = C.status_docs_public_base()
    token = url[len(base):-1] if url.startswith(base) and url.endswith("/") else ""
    if not SD.TOKEN_RE.fullmatch(token):
        raise ValueError(f"{url!r} is not a status-page link ({base}<22-char token>/)")


def bubbles(status, url):
    """-> [bubble 1, bubble 2] exactly as they would be sent (module docstring)."""
    best = best_clinic(status)
    n = len(status["sent"])
    if n < 2:
        raise ValueError(f"{n} sent clinic(s): the approved wording is plural only")
    check_url(url)
    first = "\n".join([
        P.STATUS_BEST_INTRO_DE,
        f"{best['name']}, {best['town']}",
        f"{P.STATUS_JOBS_LABEL_DE} {'; '.join(f'„{job}“' for job in best['jobs'])}",
        f"{P.STATUS_HOUSING_LABEL_DE} {best['housing']}",
        f"{P.STATUS_TRAVEL_LABEL_DE} {best['travel']}",
    ])
    second = "\n".join([P.STATUS_COUNT_DE.format(n=n), P.STATUS_LINK_LEAD_DE, url])
    return [first, second]
