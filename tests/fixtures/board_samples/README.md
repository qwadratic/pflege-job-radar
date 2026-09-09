# Career-portal samples

Small, real examples of what a vendor's raw board response actually looks like -- not a full mirror,
just enough shape to write or debug an adapter/classify test against without hitting the live site
(and without depending on it staying online or unchanged). Personal contact fields (name, e-mail,
phone of the HR contact on each posting) are redacted; everything else is the real response.

| file | vendor | source | fetched |
|---|---|---|---|
| `dvinci_list_json_sample.json` | dvinci (`crawlers.vendor_adapters.crawl_dvinci`) | `https://sozialstiftung-bamberg.dvinci-easy.com/jobPublication/list.json` (Klinikum Bamberg, clinic_id 46101) | 2026-09-09 |

4 entries picked to span the classify buckets a real board mixes: `pflegefachkraft`, `pflegehelfer`
(the "Pflegehilfskräfte" plural that patterns.json missed until this same session), `nicht_pflege`
(Assistenzarzt), `ausbildung`. Add more vendors here as similar adapter-vs-Firecrawl comparisons
(`tools/compare_adapter_fc.py`) turn up a board worth freezing a sample of.
