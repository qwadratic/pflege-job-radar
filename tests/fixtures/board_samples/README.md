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
| `easyhr_proxy_reisach_list_sample.json` | easyhr (`crawlers.vendor_adapters.crawl_easyhr`) | `https://www.reisach-kliniken.de/easyhr-proxy.php` (Reisach Kliniken -- Hochgrat-Klinik Wolfsried 77607/77672, Adula-Klinik Oberstdorf 78008/78071), all 22 live positions | 2026-09-23 |
| `easyhr_proxy_reisach_detail_sample.json` | easyhr (`crawlers.vendor_adapters.crawl_easyhr`) | `https://www.reisach-kliniken.de/easyhr-proxy.php?id=7aee700e-a911-4e73-9079-0b09fa372c20` (the Hochgrat Klinik "PFLEGEFACHKRAFT / GESUNDHEITS- und KRANKENPFLEGER" posting from the list sample above) | 2026-09-23 |
| `augencentrum_karriere_sample.html` | wp_jobs (`crawlers.vendor_adapters.crawl_wp_jobs`) | `https://www.augencentrum.de/ueber-uns/karriere/` (AugenCentrum Rosenheim, clinic_id 16307), both live postings (MFA, Pflegefachkraft) | 2026-09-24 |
| `rexx_rhoen_stellenangebote_sample.html` | rexx (`crawlers.vendor_adapters.crawl_rexx`) | `https://bewerberportal.rhoen-klinikum-ag.com/stellenangebote.html` (RHÖN-KLINIKUM AG, 67308 et al.): two unedited slices -- the `data-count="337"` listing section's opening tags and the j1232 Bad Neustadt `<article>` | 2026-09-29 |
| `rexx_rhoen_stellenangebote_start400_sample.html` | rexx | same board `?start=400`, the listing section past the last posting (the board's own end signal: no `joboffer_container`) | 2026-09-29 |
| `rexx_rhoen_detail_j1232_sample.html` | rexx | the j1232 detail page's JobPosting JSON-LD block only; two department phone numbers redacted | 2026-09-29 |
| `drv_bund_jobs_bad_brueckenau_sample.html` | drv_bund (`crawlers.vendor_adapters.crawl_drv_bund`) | `https://www.drv-bund-karriere.de/jobs?search=&field_location=47&page=0` (DRV Bund portal filtered to Bad Brückenau -- Klinik Hartwald RH1498): the listing's 9 `resultItem` blocks, each with its Ort | 2026-09-29 |
| `drv_bund_jobs_bad_brueckenau_page1_sample.html` | drv_bund | same listing `&page=1`: the board's own "keine Treffer" end | 2026-09-29 |
| `drv_bund_job_pflegefachkraft_bad_brueckenau_sample.html` | drv_bund | `https://www.drv-bund-karriere.de/jobs/pflegefachkraft-bad-brueckenau-0`, the `jobAdMainInfo` section; contact name/phone/e-mail redacted | 2026-09-29 |
| `gsb_drv_reha_klinik_saale_karriere_sample.html` | wp_jobs (`crawlers.vendor_adapters._job_link_pairs`) | `https://www.reha-klinik-saale.de/klinik/saale/karriere` (DRV Reha-Klinik Saale RH1901, Government Site Builder CMS): the two real `SharedDocs/Stellenangebote` links plus the `SiteGlobals/Forms` search-form links that were stored as phantom postings | 2026-09-29 |
| `median_jobs_bad_toelz_sample.html` | median (`crawlers.vendor_adapters.crawl_median`) | `https://karriere.median-kliniken.de/de/jobs/0/0/62/` (MEDIAN portal filtered to Bad Tölz -- Buchberg-Klinik RH2655): one contiguous slice (lines 1508-2240) with the 8 `joboffer-item` sections and the root/job-alert/Initiativ links next to them | 2026-09-29 |
| `median_jobs_bad_gottleuba_sample.html` | median | `https://karriere.median-kliniken.de/de/jobs/0/0/28/` (Bad Gottleuba, 14 postings): the 10 rendered items and the `data-more` load-more button | 2026-09-29 |
| `median_jobs_bad_gottleuba_more_sample.json` | median | that button's load-more URL (page 2): `{html, more}` with the last 4 items and no further page | 2026-09-29 |
| `median_job_j17969_sample.html` | median | `https://karriere.median-kliniken.de/de/jobs/job/Examinierte-Pflegefachkraft-mwd-de-j17969.html`, the JobPosting JSON-LD block only; contact redacted | 2026-09-29 |
| `onapply_hoehenried_stellenangebote_sample.html` | onapply (`crawlers.vendor_adapters.crawl_onapply`, a crawl_wp_jobs delegate) | `https://hoehenried.de/home/karriere/stellenangebote/` (Klinik Höhenried RH2229): the two empty `onapply-career-page-container` widgets and their `data-url` feeds | 2026-09-29 |
| `onapply_hoehenried_feed_sample.json` | onapply | `https://hoehenried.onapply.de/feed/render.html?format=json`, all 13 listings (one nursing: the Initiativbewerbung Pflegefachkraft; one grounds-keeping "Parkpflege") | 2026-09-29 |
| `onapply_cep_hoehenried_feed_sample.json` | onapply | `https://cep-hoehenried.onapply.de/feed/render.html?format=json` (the Prävention tenant), its 1 listing (Hauswirtschaft) | 2026-09-29 |
| `onapply_hoehenried_detail_90859_sample.html` | onapply | `https://hoehenried.onapply.de/details/90859.html` (the Initiativbewerbung Pflegefachkraft), the JobPosting JSON-LD block only; contact redacted | 2026-09-29 |
| `kurpark_initiativbewerbung_sample.html` | wp_jobs (`crawlers.vendor_adapters.parse_job_page`) | `https://kurpark.mutter-kind.de/stellenangebote/initiativbewerbung-102` (Reha-Klinik Am Kurpark RH1138): three unedited slices -- the `<title>`, the cookie-consent dialog's `<h1>` (first on the page) and the job's own `<h1>` | 2026-09-29 |
| `kurpark_diaetassistent_sample.html` | wp_jobs | `https://kurpark.mutter-kind.de/stellenangebote/diatassistent-(mwd)-7021`, same three slices | 2026-09-29 |
| `mediclin_roter_huegel_sample.html` | mediclin (`crawlers.vendor_adapters.crawl_mediclin`, a crawl_wp_jobs delegate) | `https://www.mediclin-karriere.de/reha-zentrum-roter-huegel/` (MEDICLIN Reha-Zentrum Roter Hügel RH2547): one contiguous slice, the `brajobsmdc_jobs` element from its filter form through the list view's 10 teaser cards ("1-10 von insgesamt 10"); the map view's duplicate cards left out | 2026-09-29 |
| `mediclin_detail_guk_sample.html` | mediclin | `.../reha-zentrum-roter-huegel/gesundheits-und-krankenpfleger-altenpfleger-wmd-4430-2607/`: the `<title>` and the page from the `<h1>` through the "Wir bieten Ihnen" section; the contact box after it left out | 2026-09-29 |
| `mediclin_gernsbach_sample.html` | mediclin | `https://www.mediclin-karriere.de/reha-zentrum-gernsbach/` (not a registry clinic -- the smallest MediClin board with a second page): same slice as Roter Hügel, 10 of 11 cards | 2026-09-29 |
| `mediclin_gernsbach_page2_sample.json` | mediclin | that listing's AJAX endpoint `.../jobs.json?tx_brajobsmdc_jobs[action]=pagination...` with the form's hidden fields, page=2, jobfilter=0: `success` plus the `appendedElements` (the 11th card), `jobslistresults`, `jobslistresultspart` entries of `html`; pagination/facilityMap/events entries left out | 2026-09-29 |
| `coveto_altmuehlfranken_jobs_sample.html` | coveto (`crawlers.vendor_adapters.crawl_wp_jobs`, routing label `coveto`) | `https://k61199.coveto.de/public/jobs/` (Klinikum Altmühlfranken 57701/57705/RH2456, 48 jobs on two pages): three unedited slices -- the `<title>`, the first table row (job 1373, Weißenburg) and the `coveto_jobs__pager` nav | 2026-09-29 |
| `coveto_altmuehlfranken_jobs_page2_sample.html` | coveto | same board `?page=2`: the `<title>`, the rows of jobs 1370 and 1353 (both Gunzenhausen) and the pager (page 2 active, no page 3: the board's own end) | 2026-09-29 |
| `coveto_altmuehlfranken_job_1373_sample.html` | coveto | `https://k61199.coveto.de/job-akademische-pflegefachkraft-b-a-b-sc-mit-weiterbildung-praxisanleiter-in-m-w-d-weissenburg-in-bayern-1373.html`: the `<title>` and the JobPosting JSON-LD block; contact name and phone redacted | 2026-09-29 |
| `coveto_altmuehlfranken_job_1370_sample.html` | coveto | `https://k61199.coveto.de/job-pflegefachkraft-chirurgie-m-w-d-gunzenhausen-1370.html`, same two slices, same redaction | 2026-09-29 |
| `coveto_altmuehlfranken_job_1353_sample.html` | coveto | `https://k61199.coveto.de/job-pflegefachmann-frau-fuer-unsere-geriatrische-rehabilitation-m-w-d-gunzenhausen-1353.html`, same two slices, same redaction | 2026-09-29 |
| `hausamkurpark_ihre_karriere_sample.html` | wp_jobs (`crawlers.vendor_adapters._title_only_job_rows`) | `https://www.hausamkurpark.de/die-klinik/ihre-karriere` (PARITÄTISCHE Haus am Kurpark RH1970): the `<title>` and one contiguous slice from the site menu (linked `<li>`s) through the bare-`<li>` "aktuelle Stellenanzeigen" list; the contact sidebar after it left out | 2026-09-29 |
| `pi_asp_klinikum_ingolstadt_list_sample.html` | pi_asp (`pflege_jobs.sources.pi_asp._list_rows`) | `https://wirkzvin.pi-asp.de/bewerber-web/?companyEid=*` (Klinikum Ingolstadt's P&I LOGA board, 16101): the rendered `.BW-PositionsScreen` DOM from headless Chromium, scripts stripped -- all 65 posting rows, 38 of them without "(m/w/d)" in the title, and the pin line naming the org unit, not a town (TASK-178) | 2026-09-29 |
| `pi_asp_regiomed_position_popup_sample.html` | pi_asp (`pflege_jobs.sources.pi_asp.AD_JS`) | the popup a title click opens on `https://logaallin.regiomed-kliniken.de/bewerber-web/?companyEid=%2a` (Sana Klinikum Coburg, OTA / OP-Pflege posting): the `.BW-webPositionDeteilScreen` DOM from headless Chromium once the form had rendered, scripts stripped -- share bar, the ad's rich-text blocks, the form's lead-in, then the first 4 rows of the application form (the rest of the form left out); the HR contact line and the xsrf token redacted (TASK-184) | 2026-10-01 |
| `pi_asp_wirkzvin_position_popup_sample.html` | pi_asp (`pflege_jobs.sources.pi_asp.AD_JS`) | same for `https://wirkzvin.pi-asp.de/bewerber-web/?companyEid=*` (Klinikum Ingolstadt, OTA / Gesundheits- und Krankenpfleger posting); the two contact lines redacted | 2026-10-01 |

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
