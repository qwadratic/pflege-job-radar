# Career-portal samples

Small, real examples of what a vendor's raw board response actually looks like -- not a full mirror,
just enough shape to write or debug an adapter/classify test against without hitting the live site
(and without depending on it staying online or unchanged). Personal contact fields (name, e-mail,
phone of the HR contact on each posting) are redacted; everything else is the real response.

| file | vendor | source | fetched |
|---|---|---|---|
| `dvinci_list_json_sample.json` | dvinci (`crawlers.vendor_adapters.crawl_dvinci`) | `https://sozialstiftung-bamberg.dvinci-easy.com/jobPublication/list.json` (Klinikum Bamberg, clinic_id 46101) | 2026-09-09 |
| `klinikum_passau_offene_stellen_sample.html` | klinikum_passau (`pflege_jobs.sources.klinikum_passau.crawl`) | `https://www.klinikum-passau.de/beruf-karriere/offene-stellen` (Klinikum Passau, clinic_id 26201) | 2026-09-18 |
| `meinkrankenhaus2030_stellenanzeige_sample.html` | wp_jobs (`crawlers.vendor_adapters.crawl_wp_jobs`) | `https://www.meinkrankenhaus2030.de/stellenanzeige-operations-technischen-assistent-w/m/d-in-vollzeit` (Krankenhaus Weilheim 19002 / Schongau 19001, shared board) | 2026-09-21 |
| `klinik_feldafing_stellenangebote_sample.html` | smartrecruiters (`crawlers.vendor_adapters.crawl_smartrecruiters`) | `https://www.klinik-feldafing.de/karriere/stellenangebote` (Benedictus Krankenhaus Feldafing, clinic_id 18813) | 2026-09-21 |

4 entries picked to span the classify buckets a real board mixes: `pflegefachkraft`, `pflegehelfer`
(the "Pflegehilfskräfte" plural that patterns.json missed until this same session), `nicht_pflege`
(Assistenzarzt), `ausbildung`. Add more vendors here as similar adapter-vs-Firecrawl comparisons
(`tools/compare_adapter_fc.py`) turn up a board worth freezing a sample of.

The klinikum_passau sample is a contiguous real slice (untouched byte order) covering 3 of the
live page's 17 postings across 2 of its 7 department groups, including the one posting (job_2685)
whose >8000-char description has neither an optional vacancy-from nor a vacancy-file tag ahead of it
-- the shape that bled into the next posting before the fixed-window bug was fixed. HR contact names,
direct phone numbers and the one contact e-mail are redacted; everything else is the real response.

The two 2026-09-21 samples are each two (resp. one) contiguous unedited slices of a much larger live
page, kept because in both cases the whole board hinged on a few bytes that are easy to lose:
`meinkrankenhaus2030_stellenanzeige_sample.html` has no JSON-LD and no heading tag of any level, so
the page `<title>` -- written "Stellenanzeige | <real title>", generic label first -- is the only
place the job title exists (TASK-52). `klinik_feldafing_stellenangebote_sample.html` carries the
b-ite `artemed-8:niiid` mount (the BITE recruiting-assistant chatbot, `createClient({key:""})`, no
postings API) next to the widget that actually lists the jobs: a SmartRecruiters config whose JSON
is entity-escaped because it lives inside a `data-widget` HTML attribute (TASK-55).
