# Frontend night session — 2026-09-09 (repo-5e)

Running report, written while the work is in progress. No commits: everything below is in the working
tree only. Adapter/ATS work was handed to the session that owns it (repo-85) — this session is frontend.

## Environment / hygiene

- `git status` before starting: clean apart from this session's own frontend changes; HEAD `c722c49`.
- Tests must run from the repo venv — the system `python3` has no fastapi:
  `.venv/bin/python -m pytest tests/ -q` → **429 passed** (baseline, before tonight's changes).

## Done

1. **`999+` badge on Jobs** — replaced with a compact number (`1.2k`, `23k`), exact value in the tooltip.
   `999+` collapsed everything above a thousand into one label, which is the only thing that badge is read for.
   `web/pro.template.html` (`compact()` next to `loadStats`).
2. **Prompts tab on Clawl** — the Firecrawl prompt templates were only visible by reading
   `pflege_jobs/sources/firecrawl_agent.py`. New read-only tab renders the jobs prompt and the career-site
   prompt (for a sample hospital, or for a hospital picked from the searchable list), each with its answer
   schema, character count and a copy button. It reads `GET /api/firecrawl/prompts`; that endpoint does not
   exist yet — repo-85 was asked to add it — so until it does the tab shows an explicit "endpoint missing"
   card, and the offline mock (`?mock=1`) serves the shape so the UI can be reviewed and tested now.

## Done (continued)

3. **Estimate wired up** — `GET /api/crawl/estimate` existed server-side with no UI at all. The run form now
   has an Estimate button next to Plan; it is enabled only for a hospital target (the endpoint is per clinic)
   and reads up to 8 boards for free, showing rows / definite Pflege / ambiguous / confidence per hospital,
   including the endpoint's honest "ambiguous" answer rather than folding it into a single number.
4. **Failure triage** — a failed run's cause lived only in the log body (run #69 wrote 97 lines that all said
   "cap 6 below the agent minimum"). The Run tab now groups the last failed run's log by cause (cap too low,
   spend gate refused, budget too thin, firecrawl.enabled off, adapter 0 rows, fetch failure), counts the
   lines per cause, and offers the implied fix: re-run the same target at 3× the cap, aim the form, jump to
   Settings, or open the log.
5. **Live run panel** — while a run is in flight the Run tab shows elapsed time (ticking), rows / new /
   credits so far and the last log lines. No cancel button: the API has no endpoint to stop a crawl run
   (the hunter kill switch only stops the hunter) — that is a backend ask, not something the UI can fake.
6. **Run this ATS vendor** — coverage rows got an action that aims the run form at `scope=ats_type` for that
   adapter and switches to the Run tab. `ats_type` scope has existed for a while with zero runs against it.
7. **Mobile** — the Clawl page overflowed horizontally at 390 px (a long mode label stretched the two-column
   grid). Fixed with `min-width:0` on the grid children, a full-width segmented target control and a capped
   `.pick` width. No horizontal scroll left at 390 px.

8. **Plan gate is a real drawer** — it was already a right-hand sheet but opened `showModal()`, so the page
   it describes was greyed out and unclickable. Now `show()` (non-modal) with Esc and click-outside wired by
   hand, 460 px, full width below 560 px. Matches the overlay rule agreed with Ivan: drawer for plan, log,
   compare and schedule editing; modal only for irreversible confirmations.
9. **Fixed a real bug found while doing it** — the plan body is built with the native `replaceChildren`,
   not `el()`, so every absent gate warning was rendered as the literal text "null" ("nullnull" in the
   drawer header area whenever neither warning applied). Now filtered.

10. **Responsive sweep** — every route of both built pages checked at 390 px and 1440 px. Two real
   overflows found and fixed: the Clawl run form (long mode label stretching the two-column grid) and the
   Settings pattern editor (`.pat` grid with a fixed 170 px first column). Public jobs filters now go
   full-width below 560 px so the role dropdown and the town search line up.

11. **Cancel a running crawl** — repo-85 shipped `POST /api/crawl/runs/{run_id}/cancel` (commit `ecb5882`),
   so the live-run panel now has it. Two-click rather than a dialog: the first click arms the button for
   four seconds, the second sends the request; 404/409 come back as a toast. `cancelled` got its own status
   dot. repo-85 then ran the live smoke (22 softgarden boards, cancelled 2 s in): the cancel is *cooperative*
   — accepted immediately, but the run stays `running` until the board in flight finishes, up to ~25 s. So the
   panel now shows "cancel requested — stops after the board in flight" and a disabled "stopping …" button as
   soon as the POST returns, instead of looking like the click did nothing. The mock models the same two-step
   behaviour (`cancel_requested` first, status flips a moment later) and the test asserts both states.
   I never fired cancel at the live service myself — that smoke was repo-85's, on a run it owned.

12. **Town picker is a map now** (`web/index.*`, home page). Dots at each town's real coordinates, radius by
    open postings, joined by a minimum spanning tree so Bavaria reads as one shape; clicking a dot narrows the
    hospital list below (click again to clear), and the searchable picker moved into the map header and now does
    the same thing instead of navigating away. Dots are focusable and operable with Enter/Space, each carries an
    `aria-label` and a `<title>`. (The Germany inset and the density silhouette this entry originally
    described were both dropped on Ivan's call — see 15.)
    Data: `web/geo_shapes.py` (new) bakes two literals out of `data/geo/gemeinden_de.csv` — the same municipality
    table `pflege_jobs/geo.py` resolves against, so the map cannot disagree with what the pipeline geocoded.
    One outline per Bundesland (grid silhouette of municipality density, closed, traced, simplified, smoothed —
    not a cadastral border) plus every Bavarian town's coordinates. Both are inlined in the page rather than
    fetched, which keeps the single-file SPA and needs no new route; it costs ~66 kB of the ~146 kB page.
    Names are indexed twice, plain and with the parenthetical dropped, because the register writes "Kempten"
    where the municipality table writes "Kempten (Allgäu)". A town that still fails to resolve is reported under
    the map ("N Orte ohne Koordinaten"), never silently dropped, and the old chip wall stays as the fallback
    when nothing resolves at all.
13. **Infinite scroll** on both public list views (`paged()`): a sentinel below the list loads the next page as
    it nears the viewport, and re-observes after each append so a short page keeps filling. The "Mehr anzeigen"
    button stays as the manual fallback — no `IntersectionObserver`, a failed fetch, or a keyboard user who
    never scrolls. The Pro tables still use `pager()`: their offset lives in the URL and the operator sorts and
    jumps around, which infinite scroll would take away. Say the word and they convert too.
14. **Clinic mark** on the hospital rows and on both clinic pages (public and Pro). Fitted whole into the tile
    (`object-fit:contain`) on the page background, so it lands on white in the public board and on black in the
    dashboard, exactly as asked. Source order: `photo_url`, `logo_url`, then the clinic's own
    `/apple-touch-icon.png` and `/favicon.ico`, then initials. **The API has no photo field yet**, so today
    every mark comes from the clinic's own icon or falls back to initials — a real photo needs a harvest step
    (og:image or the career page hero) and a column; that is backend work, not this session's. Note the tile
    fetches from the clinic's host, never from a third-party logo service.

15. **The outline is the real border now** (`tools/build_geo_outline.py`, new). The old outline was a silhouette
    traced around municipality density — a shape that looked like Bavaria without being it. Ivan asked for open
    polygon data instead, so the boundary is baked from **BKG VG2500** (Verwaltungsgebiete 1:2 500 000,
    `vg2500_01-01-2026.utm32s.gpkg`): the GeoPackage blob is parsed by hand (GP magic → envelope → WKB), the
    EPSG:25832 coordinates are inverted to WGS84 with Snyder's transverse Mercator (stdlib `math` only, asserted
    against a known point before the download), and the rings are Douglas-Peucker simplified. Bavaria is
    `AGS='09' AND GF=9` — `GF=8` is the "Bayern (Bodensee)" variant — and comes out as 2 rings / 662 points
    (the second ring is the Jungholz enclave, drawn as a hole with `fill-rule:evenodd`). 38.5 kB on disk in
    `data/geo/bayern_vg2500.json`; the seven Regierungsbezirke are baked at the same time and unused so far.
    **Licence: dl-de/by-2-0** — commercial use is allowed with attribution, so the map credits
    "© BKG (2026) · dl-de/by-2-0 · VG2500 — Geometrie vereinfacht" with BKG and the licence as links, including
    the Veränderungshinweis the licence requires because the geometry is simplified. Checked and rejected on the
    way: GADM and `isellsoap/deutschlandGeoJSON` (non-commercial, and the latter derives from GADM), Eurostat
    GISCO (non-commercial), OSM/ODbL (share-alike would force publishing the simplified coordinates).
    Natural Earth 10m is the public-domain fallback if BKG ever becomes a problem.
    Town coordinates got the same honesty pass: `web/geo_shapes.py` now mirrors the browser's normaliser in
    Python (NFD, `ß→ss`, strip non-alphanumerics) instead of reusing the CSV's `normalized_name`, which folds
    `ü→ue` where the browser folds `ü→u`; each municipality is indexed under four spellings, a spelling two
    municipalities share is dropped rather than guessed, and 21 register towns that stay ambiguous are pinned
    through the hospital register's `regierungsbezirk` (ARS digit 3). 2,243 keys, 65.5 kB inlined.

16. **Mobile map** — Ivan's complaint was "on mobile it throws me straight down and won't let me play with the
    map". Root cause, found on the recordings and not by reading: the dot handler called `sec.scrollIntoView()`,
    so the first tap jumped the page 1,184 px and every following tap landed on whatever had slid under the
    finger. The scroll hijack is gone (only the explicit "Kliniken ansehen" button scrolls now), the action row
    sits in flow so it can never reflow under the finger, dot labels thin out on coarse pointers (top 4 plus the
    selected ones instead of top 10), hover styling is behind `@media(hover:hover)` so iOS cannot stick it, taps
    hit the nearest dot within 22 CSS px rather than needing the circle itself. The recording now shows the
    scroll pinned at 838 px across three consecutive taps, each one adding to
    "13 Kliniken · München · Nürnberg · Passau".

17. **First screen** (`#hero`). Ivan asked for the trick where one word in the title keeps changing to show how
    wide the offer is. The h1 ends in a flap: "Offene Pflege-Stellen an Kliniken **in ganz Bayern**" flips to
    "in Kempten", "in Passau", "in München" … — up to 8 towns, each with its own open-postings counter set in
    mono beside it, and only towns the gazetteer can place on the map are allowed into 42 px type (that is what
    keeps "82467 Garmisch-Partenkirchen" out of the headline). The lead paragraph itself never flickers, as
    asked; the second angle lives in a line under it that wipes rather than flips, on a slower clock, and reads
    from the real facets: "203 offene Stellen in Teilzeit", "50 offene Stellen auf Intensiv- und IMC-Stationen",
    "51 offene Stellen für Pflegefachkräfte", "590 offene Stellen nach TVöD bezahlt", plus the CV ask and one
    index card. The four axes are the ones the live index actually fills: 17 `department_hint` values (Intensiv/IMC
    210, OP 153, Chirurgie/Orthopädie 106 …), 3 `employment_types` (vollzeit 762, teilzeit 696, minijob 1),
    9 `role_class` (pflegefachkraft 1383 … pflegehelfer 4) and 6 `enr_tariff` (TVöD 590, TV-L 187, AVR 156+52+20,
    Haustarif 17); every one of those 35 values has a hand-written German and English phrase. `contract` was left
    out on purpose — it has 15 BEFRISTET rows and nothing else, so a line built on it would lie. Only job-level
    facets are allowed in the deck — a clinic-level facet counted against
    "offene Stellen" would be a lie, and a test enforces it. A value with no hand-written German phrase is
    printed with its axis and the API's own label instead of being dropped, so an unmapped value shows up on the
    front page rather than in a console. Every card links to the list it promises: `#/jobs?department_hint=…`,
    `?employment_types=…`, `?role_class=…` now filter the Jobs page and show up there as a clearable chip.
    Accessibility: the flap is `aria-hidden` with a visually-hidden twin, so the h1 keeps one fixed accessible
    name while the visible word changes; the rotation freezes while the hero is hovered, focused, off-screen or
    the tab is hidden, settles on the first `pointerdown`, stops after 44 beats, and never starts at all under
    `prefers-reduced-motion` (the deck is still built — the content is not motion-dependent).

18. **Evaluation harness** (`tools/uieval.py`, new) — Ivan asked for a "tapper" that records what the agent does
    on the page, with a visible cursor, so both of us can watch. It injects an overlay (34 px cursor ring, tap
    ripples, a caption bar with elapsed time, the current step and `scrollY`), drives the page through a small
    scenario API (`say / move / tap / drag / wait`, touch or mouse), records video, and then compresses:
    `run.mp4` (H.264, crf 30, 15 fps, 390 px wide) plus `sheet.jpg`, a contact sheet of 12 evenly-spaced frames
    each stamped with its timestamp, plus `steps.json` with the step log and any page errors. A whole run is
    19–312 kB, which is what makes reading the frames affordable. Four scenarios so far — `map-mobile`,
    `map-desktop`, `hero-mobile`, `hero-desktop` — and a generated gallery.
    Artefacts land in `eval_out/<scenario>/` at the repo root — outside the served tree, gitignored,
    watched as local files (`eval_out/index.html`). Publishing to `web/skill/reviews/eval/` is an explicit
    `--publish` flag a human types, per run, and only for a page that is already public: `/skill/{name:path}`
    in `app/main.py` has no session dependency, so that tree is world-readable. The recordings originally
    written there (`pro-nav/run.mp4` and its contact sheet) were **deleted**: they were screen captures of the
    authenticated `/pro` dashboard, served 200 to anonymous callers, which handed out every recorded view of a
    page the 303 login gate is supposed to protect. Disposable either way — delete when the topic closes.

## Done (second half, 2026-09-10 into 2026-09-11)

Everything below was in the working tree only until **09:30:17 UTC on 2026-09-11**, when the parallel
security session restarted `pflege-web` and rotated the box's owner passphrase. As this is written it is
all live on `:8501`, re-measured there rather than assumed (round 7 in item 25). Two files in this half —
`app/auth.py` and `docs/auth.md` — now belong to that session: it committed them as `f43a812` together
with a per-IP login throttle. They are described here and were not edited after that commit.

19. **A real passphrase gate, and two fake doors deleted** (`app/auth.py`, `web/login.template.html` new).
    Identity is now `AUTH_DISABLED` → the `pj_session` cookie → anonymous, and nothing else. The exe.dev
    proxy header (`X-ExeDev-Email`) and the tailnet branch are gone rather than kept as a fallback:
    anything that can reach the uvicorn port directly can set that header itself, so it was a forge hole,
    not a login (`app/auth.py:14-15` carries that sentence in place). The four routes are
    `POST /api/auth/login` (`:313`, public), `PUT /api/auth/password` (`:332`, owner session only through
    `OWNER_WRITE_PATHS` at `:447`, so an agent key cannot reach it), `POST /api/auth/logout` (`:396`) and
    `GET /api/me` (`:404`). Pages stopped being advisory: `GATED_PAGES` (`:452`) 303s `/pro` and
    `/autopilot` to `/login?next=…`, and `/deck` moved into `OWNER_PAGES` (`:457`) — it had been in
    `GATED_PAGES`, which is the level a paying customer reaches through the magic link, so every customer
    could read the internal briefing. `MEMBER_API` (`:460`) and `MEMBER_READ` (`:463`) are the halves that
    need any session: the CV upload and `GET /api/postings/{id}/closed`. The login page is a real
    `<form novalidate>` with `?next=` resolved through `new URL(raw, location.origin)` — what is checked is
    the resolved string handed to `location.href`, never the input, because both earlier versions inspected
    the input and both were bypassable (`/^\/(?!\/)/` let `/\evil.example/` through; trusting `u.pathname`
    let WHATWG normalisation turn `/..//evil.example/x` into `//evil.example/x`). `tests/test_auth.py`
    collects **151** cases.
    The throttle went out and came back, and the second version is not the first one. What shipped on
    2026-09-10 was `LOGIN_PER_HOUR = 10` in a single global bucket: one shared credential and, at the time,
    no client IP anyone trusted, so ten wrong guesses from any anonymous caller answered **429 to the
    correct pair** for the next hour — measured at 60 wrong passphrases in 0.33 s giving
    `[401 ×10, 429 ×50]`. That version was reverted the same night, with both designs that would work
    written into `docs/auth.md` "Rate limits" rather than a replacement invented on the spot. The parallel
    security session then built the per-IP one and committed it as `f43a812`: `LOGIN_FAIL_PER_HOUR = 10`
    (`app/auth.py:43`) counted per `request.client.host` in a `login_failures` table, on the finding that
    `:8501` is reached directly and the raw TCP peer therefore *is* the caller. The constraint that comes
    with it: put a proxy in front of the port without `--proxy-headers --forwarded-allow-ips=…` on
    `deploy/pflege-web.service:11` and every caller collapses into one bucket again — the reverted design,
    by accident. A live check from this box at 09:34 answered **429 on the first attempt**, that hour's
    bucket for `127.0.0.1` having already been spent by the session that built it.
    No longer true as this is written: the box ran the seeded pair for six rounds and stopped at 09:30.
    `GET :8501/api/me` → `"default_credentials": false`, so a `settings.login` row exists and
    `DEFAULT_LOGIN` no longer opens the board. The red banner on `/login` and `/deck` is down on the
    deployed copy and still up on a fresh checkout, which is what `app/auth.py:165-172` promises (no row
    *is* the shipped pair). The new passphrase is not in the repo and not in this report; it lives in the
    other session's transcript, which is the one thing about it worth acting on.

20. **The map credit became a footnote — the legally required part stayed visible** (`web/references.html`
    new, `web/index.template.html`, `web/build.py`). The whole credit could not move behind "Read more":
    dl-de/by-2-0 §2 (provider and licence link) and §3 (change note), plus BKG's "deutlich sichtbarer
    Quellenvermerk", require a visible minimum wherever the geometry is shown. So the visible tier shrank
    to the shortened form BKG itself sanctions for maps — `© BKG (2026) dl-de/by-2-0, Daten verändert¹`,
    both links live — and only the dataset URI and the full deed unfold. The expanded note is a file, not
    a route: `web/references.html` is substituted into the endnote at build time through the
    `__REFERENCES_GEO__` token, and `web/build.py:13-18` refuses to build at all when that file is missing,
    because a licence notice must not depend on a fetch that can fail. The marker is
    `<a class="noteref" role="doc-noteref" aria-describedby="refs-h">` (`web/index.template.html:745`) with
    a 24×24 CSS px hit box (`:124`), and it is a **sibling** of the translated text, never a child:
    `applyLang()` overwrites `textContent` on every `[data-i18n]` node and would eat it on each DE/EN
    switch. `#refs` is `role="doc-endnotes"` (`:213`), `refJump()` (`:750`) force-opens the `<details>` and
    moves focus, and the router now ignores non-route hashes (`:336-337`) so clicking [1] no longer
    re-renders the view into a 404. One real bug fell out of doing it: the credit was mounted only from
    `townMap()`, so when no town resolved and the map fell back to `townChips()`, the whole attribution
    vanished with it. It mounts in the fallback too now. Covered by `tests/test_web_map.py` (visible tier,
    marker round-trip, fallback mount).

21. **The hero teaches by revealing, not by explaining** (`web/index.template.html`). Five states, each
    control appearing when its precondition holds and each with an explicit undo: the map and town picker,
    the chip strip plus `+ Stadt`/`Umkreis` once a town is picked, the radius slider, the specialisation
    row, and the hand-off to the clinic list. All of it lives on one object,
    `F = {bez, city, fach, withJobs, radius}` (`:537`), persisted to `localStorage["pf.home"]` (`FKEY`,
    `:538`), so a language switch — which rebuilds every tree — and a reload do not re-teach a returning
    visitor. The radius is client-side haversine over the same town coordinates the dots are drawn from
    (`circleTowns`, `:552`); `/api/clinics` has no radius parameter and needs none, and the comment at
    `:541-545` records that adding one is an upgrade path to ask about, not a default to invent. The hub is
    `F.city[0]` and leaves with it (`:581-586`) rather than sliding silently to the next chip. Dots outside
    the circle dim through the single writer of `.on`/`.dim` instead of a translucent overlay, and every
    reveal is announced through **one** live region — `<p class="say" role="status">` (`:888`) — for which
    `aria-live` came off `#view`, where it used to announce every route re-render wholesale (`:207` says
    so in place). Anonymous CV upload now gets a link to `/login?next=%2F%23%2Fcv` instead of a 401 wall.
    Covered by `tests/test_web_hero.py`.

22. **The agentic API, and an ontology that still has not landed.** The single agent key with a path-prefix
    bypass became `settings.agent_keys = {sha256: {label, scopes[], created_at, rotated_at}}`, checked in
    the ASGI middleware before the route handler, so `POST /api/crawl`'s mode gating cannot be bypassed by
    a handler bug. `SCOPES` (`app/auth.py:484`) is **8** and `AGENT_ROUTES` (`:569`) is **25**, and since
    the 09:30 restart the manifest that publishes them answers **200** on `:8501` rather than 404. The manifest and
    `GET /api/ingest/schemas` are public and generated from code (the manifest from `AGENT_ROUTES` *and*
    `required_role()`, the same table and function the middleware enforces; the schemas from
    `pflege_jobs/schema.py`), so the published contract cannot drift from the gate. Generating it is also
    what caught a leak in the route beside it: `GET /api/clinics/{clinic_id}` was publishing the same
    crawl-run rows `GET /api/crawl/runs` answers 401 for below owner — per-run Firecrawl `credits_used`,
    the internal `error` string and the last `run_log` lines — because `jobs` on the line above went
    through `D.redact()` and `runs` never did. Closed at `app/main.py:255-274`: the key stays in the shape
    and the value is `[]` below owner. `tests/test_agent_api.py` collects **60** cases.
    The ontology half did not land, and tonight it was reconciled rather than re-asserted.
    `docs/ontology.json` is still the 22-node / 40-edge bilingual graph of `HEAD` (its only diff against
    `HEAD` is one field description), `GET /api/ontology` serves that file verbatim
    (`app/main.py:1066-1068`), and no node carries `entity`, `identity`, `dead` or `populated`.
    `tests/test_ontology.py` had been written on 2026-09-10 against the rewrite and failed 4 of its 10
    cases with `KeyError: 'dead'`, `'enum'` and `'career_profile'`. The four were rewritten against the
    file that ships — not deleted, and not made vacuous: the graph may not name a column the table does not
    have (the union of `OBS_COLUMNS` in `pflege_jobs/schema.py` and the DDL at `sql/001_schema.sql:84`),
    it may not advertise a column no producer fills, `config.EXCLUDED_ROLE_CLASSES` must equal the four
    classes in `pflege_jobs/patterns.json` rather than the 3-class fallback at `pflege_jobs/config.py:94`,
    and `career_profiles` must stay empty for as long as no node publishes it. `pytest
    tests/test_ontology.py` → **10 passed in 0.13 s**, and each of the four was mutation-checked on its own
    (publish `salary_min`, publish `employer_rating`, force the fallback, insert a row into a throwaway
    copy of `app.sqlite` — four reds, one per test, and the repo's own files untouched).
    Two things the reconciliation turned up. The old helper only looked at `return {...}`, so it saw 3 of
    the **6** observation literals under `pflege_jobs/sources/` (`bite.py:182`, `board_csv.py:70`,
    `career_crawl.py:284`, `feeds.py:17`, `inbox.py:38`, `pi_asp.py:142`) and called **15** columns dead;
    across all six it is **11**, because `bite.py` and `feeds.py` fill `lat`/`lon` and `bite.py` fills
    `last_modified` and `salary_note` from the board's own fields. And `career_profiles` holds 0 rows in
    `data/app.sqlite`, which is what makes leaving it out of the graph honest today and a hole the day a
    row lands. Restoring the rewrite is now a decision about what `GET /api/ontology` should promise —
    nothing in the repo is waiting on it, and nothing consumes those keys (`web/pro.template.html:1246`
    draws nodes, edges and fields).

23. **pstack, vendored as six files rather than installed** (`.claude/skills/`). Six directories copied
    verbatim from [cursor/plugins/pstack](https://github.com/cursor/plugins/tree/main/pstack) (MIT,
    manifest v0.15.1, commit `7366ac1`), with the licence beside them in `PSTACK-LICENSE` and the whole
    reasoning in `PSTACK-VENDORED.md`: `principle-prove-it-works`,
    `principle-test-behavior-not-implementation`, `principle-fix-root-causes`,
    `principle-boundary-discipline`, `principle-sequence-verifiable-units` and `blast-radius`. The only
    edit to any of them is one line — `disable-model-invocation: true` removed from the five
    `principle-*` skills so they can load when they apply; `blast-radius/SKILL.md` is the only file under
    `.claude/skills/` that still carries the key, because it is a workflow you invoke on purpose.
    What was deliberately left out, and why (the plugin is not installed and no marketplace is
    registered): **the `SessionStart` hook** both Claude ports register, which injects an
    `<EXTREMELY_IMPORTANT>` block ordering every session into `pstack:poteto-mode` — it would land beside
    this machine's existing caveman/ponytail SessionStart setup, and a plugin update restores it after any
    manual delete; **`setup-pstack`**, which writes `~/.claude/pstack-models.md` and edits
    `~/.claude/CLAUDE.md` (global config is a human's call); **the multi-model playbooks** (`poteto-mode`,
    `arena`, `swarm`, `interrogate`, `how`, `why`, `teach`, `architect`), which shell out to the `claude`,
    `codex` and `grok` CLIs — credits are this repo's binding constraint and
    `.claude/workflows/judge-runner.js` already covers multi-lens review;
    **`principle-never-block-on-the-human`**, whose "proceed, and let the human course-correct after the
    fact" is the exact opposite of this repo's `CLAUDE.md` rule; **`no-comments` / `comment-sicko`**, which
    would strip the docstrings that *are* the method in `tests/adapter_contract.py` and `app/coverage.py`;
    **`make-bot-ui`**, which pipes `tailscale.com/install.sh` into `sudo sh` and binds the served page to
    `0.0.0.0`; and **`create-verification-skill` / `maintain-verification-skill`**, the generated
    feature-map pair — this repo already has the harder version of that idea (a live board as oracle in
    `tests/test_adapter_completeness.py`, evidence on disk in `crawl_snapshots/`, and mutation tests that
    prove each check can go red), so what was taken from them is method, written into
    `docs/feature-matrix.md`: the triage split, the three run outcomes, "never edit product code during a
    verification run", and "a cleanup that eats the proof fails the step".

24. **The feature matrix, and why publishing it empty is the point** (`docs/feature-matrix.md`,
    `app/coverage.py`, `tools/cells_from_pytest.py`, `data/feature_cells.jsonl`). The rows were frozen
    before any cell was written, and they are not invented per run: the five checks that already exist in
    `tests/test_adapter_completeness.py`, each with a matching breakage in `tests.adapter_contract.
    MUTATIONS` — `read_path_coverage`, `declared_total_parity`, `field_completeness`, `public_url`,
    `round_trip`, weighted in one line at `app/coverage.py:32`. One worker per column (board), never per
    row, because the expensive part is fetching the board, not judging the feature. Cells carry evidence —
    path, quote, `fetched_at` — never prose; the store is append-only JSONL, so a merge is `>>`, and
    `tools/cells_from_pytest.py` does the folding with an offline self-check (`--selfcheck` →
    `selfcheck ok: 4 cells`). Scoring is last and computed, so re-weighting costs zero crawls, and
    `unknown` ("looked, could not tell") and `not_checked` ("nobody looked") are counted but never scored
    (`app/coverage.py:33`), which is what keeps an unexamined adapter from reading 0 %.
    `data/feature_cells.jsonl` is **0 lines**: `pytest-json-report` is not installed and no new dependency
    was allowed, so there is no report to fold, and the completeness suite is network-marked, so no live
    crawl was run to produce one. Nothing was invented to fill the column — it reads "not checked" for
    every adapter. Six candidate rows from the brief (`pagination_followed`, `detail_fetch`,
    `feed_available`, `robots_blocked`, `method_justification`, `completeness_verdict`) were deliberately
    left out of the vocabulary: nothing produces them yet, so they would add a column of `not_checked`
    with no path to filling it.

## Verification log — seven rounds over the deck, and what is still open

The deck at `/deck` (`web/deck.template.html`, built to `web/deck.html`) is the artefact that gets
re-verified: it states numbers, and the tree keeps moving under them. Seven readings so far. Rounds 1–5 are
reconstructed from the deck's own header comment, which each round rewrote, and from the files those
entries name — I did not run them; rounds 6 and 7 are this session's and every number in them was re-read here.

**Round 1 (2026-09-10, ~18:25 UTC).** The deck written from a live read: `GET /api/stats` and
`GET /api/facets` on `:8501`, snapshot `2026-09-10T18:25:43Z`. Nothing verified twice yet.

**Rounds 2–4 (2026-09-10 into 2026-09-11).** Three passes over the same slides against the code. They
corrected: the login door had been throttled (slides 2, 13, 16); `MEMBER_API` had grown `/api/postings`;
the manifest publishes `public` / `session_only` / `redacted`; `POST /api/postings/{id}/closed` is member
and `/api/stripe*` was never owner-gated at all; the security pass became six sentences and named the
published anon key as a second, un-redacted door; five "next" items had closed themselves.

**Round 5 (2026-09-11, morning).** The round that moved the most. The login throttle was **reverted** as a
denial of service on the only door; slide 7 became a retraction, because `docs/ontology.json` was still
the 22-node / 40-edge file and `tests/test_ontology.py` was failing 4 of 10 against it;
`GET /api/clinics/{clinic_id}` was closed to anonymous crawl-run rows; the CSP that arrives with the
restart was measured to block the clinic logo of **161 of 407** hospitals (`docs/errors.md`, "HANDOFF");
the box was found to be still the 18:02:42 process; test counts were re-collected
(`tests/test_auth.py` 151, was 128; `tests/test_agent_api.py` 60, was "113" — a number pytest does not
produce here).

**Round 6 (2026-09-11, 08:25 UTC — this session).** Five things moved:

- *Slide 1 did not fit the screen it is read on.* At 1280×900 with the red default-credentials banner up
  (the live state), the bar is 109 px and slide 1 measured **919 px** against a 900 px viewport.
  `html{scroll-snap-type: y proximity}` snaps to the *end* edge of a snap area taller than the snapport,
  so a reader who stopped inside slide 1 was dragged back down. Fixed in the box, not by weakening the
  snap: `.nums` went from `minmax(148px,1fr)` to `minmax(128px,1fr)`, so the eight tiles are one row of
  129×107 px instead of two, and the slide's content is 591 px inside the 683 px the paddings leave.
  `tests/test_web_deck.py::test_slide_one_fits_the_viewport_with_the_banner_up` is new and asserts exactly
  that state.
- *The board numbers were a day old.* Re-read at 08:25: **3,203** open postings (was 2,656), 1,177 fresh,
  222 hospitals hiring, 3,916 Firecrawl credits of 8,000, board snapshot `08:17:12Z`, run **#82** done at
  03:12:59. The credit figure is the account's own `remainingCredits` and has moved 2 since yesterday's
  read while `firecrawl_usage` has had no row since 2026-09-09 — nothing this box ran booked them.
- *Two data-loss holes joined the security pass, which is now three slides and eight numbered facts.* An
  empty `PUT /api/settings/patterns` destroyed `pflege_jobs/patterns.json` earlier today: the checker only
  asked whether the regexes compile, and `{}` has none that fail, so it validated, was written over the
  file and answered `200 {"reloaded": false}` — 14,131 bytes and 9 sections replaced by 2 bytes, after
  which every classifier died with `ValueError: patterns: missing section 'employer'`. And
  `GET /api/jobs` capped every answer at 2,000 rows while echoing the cap back as the request. Both are
  closed in the tree (`app/settings.py:70-131`, `app/data.py:128-155`) and **neither fix is running**: the
  copy on `:8501` is older than both files. Measured there at 08:25:
  `GET /api/jobs?limit=999999` → `{"total": 3203, "limit": 2000}` with 2,000 rows; in-process the same
  call answers `{"total": 3203, "limit": 999999}`.
- *The ontology tests were reconciled* (item 22): 4 failed / 6 passed → **10 passed**, four mutations
  proving each rewritten case still goes red, and the specified dead-column count corrected from 15 to 11.
- *Four `file:line` references had drifted* under the deck and were re-read: `app/settings.py:200-201`
  (was `:162-163`), `app/data.py:237` (was `:189`), `app/auth.py:325` / `:353` for the magic-link pair
  (was `:355` / `:383`), `app/main.py:255-274` for the clinic-runs fix (was `:249-268`). That is the
  fourth round in which a reference moved, so it is a test now:
  `test_every_file_line_reference_in_the_deck_still_exists` walks every path-qualified reference in the
  template and fails on one that points past the end of its file. It cannot check that the line still
  *says* what the slide claims — that stays a re-read.

The three mutation baselines the deck tests quote were re-measured on today's 17 slides, because they move
with the copy and a stale number in a docstring is the same lie as a stale number on a slide: putting the
nav pill back hides **49** text runs on 16 slides at 390 px and 20 on 10 slides at 768 px (0 at 1280 px,
where it hid 2 last round), against **0, 0, 0** as it ships; restoring `pre{white-space:pre}` and
`overflow-wrap:normal` puts 5 elements over their box at 390 px and 1 at 768 px, against none as it ships;
and removing the `9ch` floor breaks 65 short words in the identifier columns instead of 28, down to `GET`
(3) and `PUT` (3). The `settle()` mutation (empty body plus the old `scrollend` listener) still leaves the
counter reading `2 / 17` while the reader sits in slide 1 at y=303 with 0 `scrollend` events fired; as it
ships, `1 / 17` at the same y.

**Round 7 (2026-09-11, 09:33 UTC — this session).** Forced from outside: the parallel security session
committed `f43a812`, restarted the unit and rotated the passphrase, so most of what six rounds had recorded
as "written, not running" had to be re-measured rather than re-stated.

- *The box is no longer the 18:02:42 process.* `systemctl status pflege-web`: active since
  `2026-09-11 09:30:17 UTC`, PID 631797, newer than every file this report describes. Re-read on `:8501` at
  09:33: `GET /api/agent/manifest` **200** (8 scopes), `GET /api/ingest/schemas` **200**,
  `GET /api/schedules` **401** anonymous where it answered 200 for six rounds, `GET /api/stats` carrying
  `content-security-policy`, `x-content-type-options`, `x-frame-options: DENY` and `referrer-policy`, and
  `PUT /api/settings/patterns` **401** to a body sent with no session at all. `GET /api/jobs?limit=999999`
  → `{"total": 3462, "limit": 999999}` with all 3,462 rows; the NDJSON form streams 3,462 lines under
  `content-range: rows 0-3461/3462`. Both data-loss repairs (items 3 and 4 of the deck's security pass) are
  therefore live, and the six-round "neither fix is running" sentence is retired.
- *The login throttle is back, per source IP,* and the passphrase is rotated — item 19 carries both, and
  neither is this session's work.
- *The CSP logo claim is withdrawn.* Round 5 measured `img-src 'self' data:` blanking the clinic mark of
  161 of 407 hospitals. What ships is `img-src 'self' data: https:`, and `clinicPhoto()` builds every source
  through `httpsUrl()`, which rewrites an `http://` logo host to `https://` before the `<img>` exists. The
  measurement described a policy that never reached the tree.
- *The deck was re-read against the restarted port* (slides 1, 2, 6, 13, 14, 15, 16, 17 all changed) and
  two `file:line` references were re-anchored after the auth commit moved them: `app/auth.py:254` for the
  `Secure` cookie (was `:234`) and `:349` / `:377` for the magic-link pair (was `:325` / `:353`).
  `tests/test_web_deck.py` is **16 passed**; the print test caught the rewrite growing slide 2 to 1,074 px
  against the 1,047 px A4 box (17 slides, 18 pages) before the copy was trimmed back to 908 px.
- *Two frontend tests were failing on selectors the hero rewrite had moved,* and both were selector fixes,
  not product ones: the chosen-town chips are `.mapbox .ctl .sel` now, not `.filters .sel`, and `.filters`
  on `#/jobs` holds two `.pick` listboxes (role, then town), so a bare `.filters .pick .dd` is a Playwright
  strict-mode violation rather than a duplicate-picker bug. Offline suite after both:
  **955 passed, 1 skipped** (`-m "not network and not completeness and not mutation"`, 5 m 25 s).

**Still open after seven rounds**, all verified in round 7 rather than carried forward:

- ~~The unit has not been restarted and runs the shipped passphrase.~~ **Closed at 09:30:17 on
  2026-09-11** by the parallel security session: restarted, rotated, and re-measured above. What is left
  of it is bookkeeping — the new pair exists only in that session's transcript, and live sessions survive a
  passphrase change (they key on the cookie HMAC), so anything older has to be kicked out by deleting the
  `sessions` rows or rotating `SESSION_SECRET`.
- The published anon Supabase key still reads every table in the `pflege_jobs` schema, recruiter e-mail
  addresses included; the redaction that shipped guards the app door only. The migration exists and is
  **not applied** (`sql/011_PENDING_anon_scope.sql`), because applying it as written takes the app's own
  reads down with it — `app/data.py:237` selects `JOB_COLS`, which contains `enr_contact_emails`.
- The ontology rewrite: a decision now, not a repair.
- The feature matrix: 0 cells, waiting on either `pytest-json-report` or a `--junit-xml` reader, then one
  live completeness run.
- The smaller ones, unchanged: the `Secure` cookie over plain http (`app/auth.py:254`), the default scope
  set for a key minted without `scopes=`, idempotency-key retention, `run.scope = "inbox"` bypassing
  `targets.SCOPES`, relative problem `type` URIs, and which code-only vocabularies join
  `data/registry/taxonomy.json`.
- The rate-limiter design is no longer one of them: answered per-IP in `f43a812`. What stands in its place
  is a deploy constraint — the key is `request.client.host`, so a proxy in front of `:8501` without
  `--proxy-headers --forwarded-allow-ips=…` on `deploy/pflege-web.service:11` turns it back into the
  global bucket that was reverted.


26. **The Kosten tab left the simple view** (`web/index.template.html`). Ivan asked for it on 2026-09-11
    after round 7: `/` is the public page, and a spend dashboard is an operator view. Removed whole rather
    than hidden — the nav entry and its USD badge, the `#/billing` / `#/kosten` route, `pageBilling()` and
    every helper only it called (`normBilling`, `billToday`, `usd`, `cmp`, `fmtDT`, `bLabel`, `MON`,
    `B`, `C_BILL`, `C_FREE`, `USD_PER_CREDIT`, `BILL_TODAY`), the `/api/billing` branch of `mockApi()`, the
    `.bl-*` / `.tiles` / `.tile` / `.meter` / `.badge` / `.tag.red` / `.tag.neon` CSS with its three
    responsive tails, and 48 i18n keys per language (`n_billing`, the `b_*` family and the `w_*` window
    keys, which were billing-only too). 117 lines out of the template. What stayed: `ME` and the
    `GET /api/me` bootstrap (`signedIn()` gates the CV upload), `renderNav()` itself, `fmtD()` — the
    lookalike of the deleted `fmtDT()` — and `.tag` / `.tag.acc`, which the job rows and filter chips use.
    The DE and EN dicts were re-counted after the cut: 125 keys each, no side-only key.
    `#/billing` and `#/kosten` now `location.replace("#/")`, which is what the router already does with the
    other retired page (`/cities`, `web/index.template.html:320`) rather than a new rule invented for this
    one; the bare `404` branch is still what an unknown path gets. Verified in Chromium with
    `GET /api/me` mocked as owner: no Kosten entry in either language at 390, 768 and 1280 px, no
    `pageerror` on `#/`, `#/jobs`, `#/cv`, a clinic page, `#/billing` or `#/kosten`.
    `/pro` keeps its billing page untouched (6 references, `web/pro.template.html:500` in the NAV list) and
    `GET /api/billing` is unchanged — this was a frontend removal, not an API one. Frontend suites
    **107 passed**, full offline suite **955 passed, 1 skipped**.

## Notes for the backend session (repo-85)

- `GET /api/firecrawl/prompts` is live (their commit `3e4d206`) and the Prompts tab was verified against the
  real endpoint, not just the mock: `.venv/bin/python -m uvicorn app.main:app --port 8899` plus the owner
  header renders the real 1,936-character jobs prompt.
- Small nit in the placeholder rendering: with no `clinic_id` the prompt reads "…, Bavaria, Bavaria, Germany"
  (the town placeholder already says Bavaria).
- Both asks landed in `ecb5882`: the cancel endpoint (wired, see 11) and the `<town>` placeholder fix.

## Handed off


- Adapter-by-adapter improvement loop → repo-85 (the ATS session), with the Firecrawl prompt-library
  proposal and a warning not to touch this session's uncommitted paths.
