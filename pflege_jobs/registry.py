"""Link postings to Krankenhausplan sites (clinic_id = KeZ).

Deterministic, rule-recorded, conservative (no match beats a wrong match):
  R1 exact      normalized employer name == normalized site name            score 1.0
  R2 operator   normalized employer name == normalized operator, unique site or site town == posting city   0.95 / 0.9
  R3 tokens     significant-token Jaccard >= 0.6 between employer and site name AND town == city          0.8
  R4 tokens_op  same against operator name AND town == city                                              0.75
  R5 loose      Jaccard >= 0.5 with town == city and the site is the only candidate in that town          0.6
Never links when >1 candidate survives a rule. Manual overrides: set postings.clinic_match_rule='manual' (untouched by re-runs).
"""
import re
from collections import defaultdict

from .schema import CLINIC_SPEC

from .classify import employer_norm as _employer_key, norm_text
from .sources.career_crawl import _canon_town


# Public-law legal forms. classify's legal-form list only knows the private-law ones (GmbH, AG, e.V. ...);
# toks() below already drops these (STOP, ALIASES["ku"]), the exact R1/R2 keys did not. TASK-169: the
# Krankenhausplan spells 'KU Bezirkskliniken Mittelfranken, AöR', the RHV Reha rows and every posting
# 'Bezirkskliniken Mittelfranken' -- keyed apart, R2_operator saw only the Reha rows in Erlangen/Ansbach.
PUBLIC_LAW_FORMS = {"ku", "gku", "aör", "adör", "kdör", "aoer", "adoer", "kdoer"}


def employer_norm(name):
    """classify.employer_norm (the employers table's identity key) keeps hyphens; here, matching a
    posting to a registry site, a hyphen and a space spell one name. TASK-168: the RHV Reha row RH1962
    and every posting's JSON-LD say 'RHÖN-KLINIKUM AG', the Krankenhausplan rows of the same campus
    'RHÖN KLINIKUM AG' -- keyed apart, R2_operator saw RH1962 as that operator's only site."""
    return " ".join(t for t in _employer_key(name).replace("-", " ").split() if t not in PUBLIC_LAW_FORMS)


STOP = {"klinik", "kliniken", "klinikum", "krankenhaus", "gmbh", "ggmbh", "ag", "kg", "ev", "e", "v", "gku", "aör", "aoer",
        "stiftung", "gemeinnützige", "gemeinnuetzige", "und", "der", "des", "die", "für", "fuer", "im", "am", "an", "in", "von", "st", "sankt",
        "fachklinik", "fachkliniken", "gesundheit", "medizinisches", "zentrum", "personalabteilung", "bereich", "campus", "standort", "haus", "recht", "rechts" if False else "recht", "stadt", "landkreis", "kreis", "bezirk", "des", "öffentlichen", "anstalt", "körperschaft"}
CITY_ALIASES = {"münchen": {"münchen", "muenchen", "munich"}, "nürnberg": {"nürnberg", "nuernberg"},
                # "i.d." (= "in der") folds fine via _canon_town's own connector-stripping when the
                # qualifier after it is spelled out ("Weiden i.d. Oberpfalz" -> "weiden oberpfalz",
                # _town_match's own docstring example) -- but clinic 37301's live registry town
                # abbreviates the qualifier too ("Neumarkt i.d.OPf."), which _canon_town has no reason
                # to know means "Oberpfalz". Found live 2026-09-21: TASK-81's R1_exact town gate
                # started comparing this town for the first time (R1_exact never checked city before)
                # and refused a real "Klinikum Neumarkt" posting stating the spelled-out city, because
                # the two forms canonicalized to "neumarkt opf" vs "neumarkt in oberpfalz" -- disjoint.
                "neumarkt in oberpfalz": {"neumarkt i.d.opf.", "neumarkt i.d. opf."},
                # TASK-170: the RHV registry spells Klinik Höhenried's town "Bernried/Obb." (RH2229); its
                # own board and every posting name the municipality, "Bernried am Starnberger See".
                "bernried starnberger see": {"bernried/obb."},
                # TASK-172: Klinikum Altmühlfranken's coveto board names the town "Weißenburg in Bayern",
                # the registry "Weißenburg i.Bay." (57701, 57706).
                "weißenburg bay": {"weißenburg in bayern"}}


ALIASES = {"universitätsklinikum": {"universität"}, "uniklinikum": {"universität"}, "uniklinik": {"universität"},
           "lmu": {"ludwig", "maximilians"}, "tum": {"technischen"}, "technische": {"technischen"}, "fau": {"friedrich", "alexander"},
           "adör": set(), "aör": set(), "anstalt": set(), "öffentlichen": set(), "rechts": {"rechts"}, "betriebsstätte": set(), "gku": set(), "ku": set()}


def toks(s):
    s = norm_text(s or "")
    s = re.sub(r"[^\wäöüß ]", " ", s)
    out = set()
    for t in s.split():
        if len(t) <= 2 and t not in ("ku",) or t in STOP: continue
        if t in ALIASES: out |= ALIASES[t]
        else: out.add(t)
    return out


KINDS = {"klinik", "kliniken", "klinikum", "krankenhaus", "krankenhäuser", "kreisklinik", "kreiskliniken", "kreiskrankenhaus", "fachklinik",
         "bezirksklinikum", "bezirkskrankenhaus", "universitätsklinikum", "uniklinikum", "hospital", "spital", "klinikverbund"}


def kinds(s):
    return {t for t in re.sub(r"[^\wäöüß ]", " ", norm_text(s or "")).split() if t in KINDS}


def overlap(a, b):
    """overlap coefficient: |A∩B| / min(|A|,|B|) — robust to long legal suffixes on either side."""
    return len(a & b) / min(len(a), len(b)) if a and b else 0.0


def city_key(c):
    """Full canonical town, not just its first word -- truncating to one word (the old
    `.split()[0]` fallback) collapsed every "Bad *" town (19 distinct places / 27 clinics) and
    several differently-qualified "Neustadt *" towns into one bucket, attributing postings to a
    clinic 100-250 km away (confirmed live 2026-09-18). career_crawl._canon_town already folds
    connector words (an der/a.d./am/bei/...) while KEEPING the geographic qualifier noun that
    follows one ("Neustadt an der Donau" -> "neustadt donau", not the bare "Neustadt" three other
    real Bavarian towns also share) -- exactly the town-identity signal city_key needs to keep."""
    c = norm_text(c or "")
    c = re.sub(r"^\d{5}\s+", "", c)                # '82467 Garmisch-Partenkirchen'
    c = re.sub(r"\s*(,|\().*$", "", c)            # 'Landshut, Isar' -> 'landshut'
    # 'Markt Indersdorf' is the LEADING-qualifier mirror of the trailing-qualifier case
    # _town_match already handles: a posting names the town alone ('Indersdorf'). Stripped here
    # (not via a _town_match generalization) because a generic prefix-or-suffix _town_match would
    # also bridge real, DIFFERENT towns that happen to share a last word (measured live 2026-09-21
    # against all 407 clinics: 'Coburg' vs 'Neustadt bei Coburg', 'Pegnitz' vs 'Lauf an der
    # Pegnitz' -- both distinct real places, ~15-30km apart). 'markt' is the only leading qualifier
    # measured in the live registry (clinic 17402); unlike 'Bad' it is never itself part of a bare
    # town's identity, so stripping it cannot collapse two different real towns together.
    c = re.sub(r"^markt\s+", "", c)
    for k, al in CITY_ALIASES.items():
        if c in al: return k
    return _canon_town(c)


def _town_match(a, b):
    """a and b are both city_key() outputs. Equal, or one is the other's whitespace-delimited
    prefix -- a registry town's canon form can carry a trailing geographic qualifier a posting's
    own city string may not repeat (city_key('Weiden i.d. Oberpfalz') == 'weiden oberpfalz', but a
    posting naming just 'Weiden' must still match it; nothing else in the registry starts with
    'weiden ', so this cannot silently absorb an unrelated town)."""
    if not a or not b: return False
    if a == b: return True
    shorter, longer = (a, b) if len(a) < len(b) else (b, a)
    return longer.startswith(shorter + " ")


def jaccard(a, b):
    return len(a & b) / len(a | b) if a and b else 0.0


# employer_norm() values whose vendor feed reliably states the wrong city -- the operator's
# registered/legal address, not the posting's real work site -- confirmed live 2026-09-23 (TASK-96):
# KJF Klinik Hochried (clinic 18006, Murnau) posts through josefinum.softgarden.io, the SAME shared
# board as its sibling Fachklinik KJF Josefinum (76110, Augsburg); every Hochried-employer posting on
# that board states city=Augsburg regardless of it being Hochried's own name in the employer field --
# the Diözese Augsburg's registered address, not Hochried's real site. Not a generalizable gate
# relaxation: scanned all 25 live other_town_disagrees refusals this session
# (tools/task96_scan_r1_exact_disagreements.py, backups/task96-r1exact-disagreements-2026-09-23.json)
# and 13 of 25 are genuine multi-site ambiguity (RoMed Klinikum Rosenheim / Bad Aibling, the gate's
# own flagship case) or a genuinely wrong employer_name extraction the gate correctly catches (TUM/
# Klinikum Rechts der Isar mislabelled on Universitätsklinikum Augsburg's own board) -- a blanket
# "ignore city disagreement" rule would silently readmit those. This is a named, single-employer
# exception, not a heuristic; add another entry only after the same live-evidence bar (a confirmed
# per-posting city that the vendor's own feed cannot correct, not just an assumption).
CITY_UNRELIABLE_EMPLOYERS = {"kjf klinik hochried"}


def _named(tokens, key):
    """True when `tokens` (what a posting says) cover `key` (what a site is called): the acceptance the
    R3/R4 rungs below apply."""
    o = overlap(tokens, key)
    return o >= 0.8 or (o >= 0.6 and len(tokens & key) >= 2)


def _pick_site(cands, texts, note):
    """Which of several same-operator sites a posting belongs to (rule R6) has to come from the posting,
    not from bed counts (2026-10-01: München Klinik ads for Harlaching + Schwabing sat on Bogenhausen, the
    biggest; Nürnberg Campus Süd ads on Nord). A site is named when the words that set it apart from the
    other candidates occur in `texts` (the employer text, title and description, or the posting's own
    listing of its locations). Candidates with the same name are one site entered twice (Plan-KH row and
    its RHV Reha twin, a Vertrags-KH placeholder): no text can tell them apart, the one with most beds
    stands for the group (decision-4).
      the text names one site -> that site
      it names none or several -> None: the posting stays unmatched and `note` says why. An operator's
      own name, or a site name another one only extends ('Klinikum am Europakanal' next to '... Neurologische
      Rehabilitation'), says no more about which site it is than a bed count does."""
    groups = defaultdict(list)
    for x in cands: groups[frozenset(x["_ntoks"])].append(x)
    biggest = lambda g: max(groups[g], key=lambda x: x.get("beds") or 0)
    if len(groups) == 1: return biggest(next(iter(groups)))
    common = frozenset.intersection(*groups)
    text = toks(" ".join(t for t in texts if t))
    named = [g for g in groups if g - common and _named(text, g - common)]
    if len(named) == 1: return biggest(named[0])
    why = ("R6 refused: sites " + ",".join(sorted(x["clinic_id"] for x in cands)) + " tie and the text names "
           + ("several of them" if named else "none of them"))
    if why not in note: note.append(why)


class Matcher:
    def __init__(self, clinics):
        self.clinics = clinics
        self.by_id = {str(c["clinic_id"]): c for c in clinics}
        self.by_name = defaultdict(list); self.by_op = defaultdict(list); self.by_town = defaultdict(list)
        for c in clinics:
            self.by_name[employer_norm(c["name"])].append(c)
            if c.get("operator"): self.by_op[employer_norm(c["operator"])].append(c)
            self.by_town[city_key(c.get("town"))].append(c)
            tk = set(city_key(c.get("town")).split()) | toks(c.get("town"))
            c["_ntoks"] = toks(c["name"]) - tk; c["_otoks"] = toks(c.get("operator")) - tk; c["_kinds"] = kinds(c["name"]) | kinds(c.get("operator"))

    def _by_town(self, ck):
        """Every registry clinic in ck's town, using _town_match's prefix rule -- a plain dict
        lookup on the exact city_key would miss a registry town whose canon form carries a
        trailing qualifier a posting's own (unqualified) city string doesn't repeat."""
        if not ck: return []
        return [c for k, cs in self.by_town.items() if _town_match(k, ck) for c in cs]

    def match(self, employer, city, board=None, description=None, employer_inherited=False, city_inherited=False,
              title=None, foreign=None, note=None, sites=None):
        """Priority: content match first (employer/operator fuzzy, then a JD-text mention) -- reliable
        regardless of which board hosted it. Board membership is a fallback ONLY, for the case content
        can't disambiguate (one generic employer name shared by every site on a group board, e.g. kbo).
        Board-first was tried and reverted: it forced a guess on shared boards that host non-Bavaria
        entities too (Artemed/smartrecruiters), instead of correctly leaving them unmatched.

        A posting is attached to a clinic only on evidence that it belongs there: its own place or facility,
        read off the posting, agrees with the clinic; or its board is demonstrably that clinic's own
        single-site board (_match_board). A value the crawler copied from the seed clinic is none of that.

        employer_inherited=True means the crawler did not read that employer off the posting -- it
        substituted the seed clinic's own registry name (pflege_jobs/sources/inbox.py records this as
        employer_source='seed'). Matching a clinic against the name we copied FROM that clinic is
        circular: it always succeeds, on every posting of a nationwide board, which is how every AMEOS
        posting in Germany attached to Neuburg (decision-5 left this open as a crawler-layer problem;
        with the marker it can finally be enforced here). Such a row must earn its clinic from the
        city/tokens/board instead.

        city_inherited=True is the same problem for the place (pflege_jobs/sources/inbox.py's
        city_source='seed', set when a job page names no location at all and the crawler substituted the
        seed clinic's own registry town, TASK-81 mechanism #3): every rule that compares the posting's town
        with the registry then "agrees" with the seed by construction. The copied town is dropped before any
        rule sees it -- until 2026-10-01 only the board fallback dropped it, and the content rules (R3_tokens:
        klinikum-straubing.de, 34 rows) re-admitted the seed clinic with a seed employer plus a seed town.

        title is the posting's own title, read with the description for R6/_match_jd. foreign is what the
        posting's board names beyond the towns of its own clinics (Matcher.foreign_places); note, a list, is
        filled with why a posting that reached a rule stays unmatched. sites is the posting's own listing of
        its locations (München Klinik's allJobs[i].locations[].title, verbatim): when there is one it names
        the site for R6 in place of the free text, whose group boilerplate lists every site."""
        notes = [] if note is None else note
        if city_inherited: city = None
        texts = sites or (None if employer_inherited else employer, title, description)
        r = self._match_content(employer, city, description, employer_inherited=employer_inherited, texts=texts, note=notes)
        if r: return r
        if r is False: return None         # sites of one operator tie and the text names no single one: the board cannot say either
        if board:
            en = "" if employer_inherited else employer_norm(employer or "")
            et = set() if employer_inherited else toks(employer)
            return self._match_board([self.by_id[i] for i in map(str, board) if i in self.by_id], en, et, city_key(city),
                                     foreign=foreign, texts=texts, note=notes)
        return None

    def foreign_places(self, pool, places):
        """The places (city_key strings) a board's postings name that are none of its clinics' towns. A board
        that names such a place serves more than its own clinics (a group portal, an aggregator, a regional job
        pool): a posting of it that names no place of its own proves nothing about the clinics in its pool."""
        towns = {city_key(self.by_id[str(i)].get("town")) for i in pool if str(i) in self.by_id}
        return {p for p in places if p and not any(_town_match(t, p) for t in towns)}

    def _match_content(self, employer, city, description=None, employer_inherited=False, texts=(), note=None):
        """(clinic_id, rule, score); None when employer, place and text name no site (the board may still decide);
        False when several sites of one operator tie and the text does not single one out (R6 refused: a board
        registered for one of those sites, typically the group's whole job market, cannot decide it either)."""
        note = [] if note is None else note
        en = employer_norm(employer or ""); et = toks(employer); ck = city_key(city)
        if not en: return self._match_jd(description)
        c = [] if employer_inherited else self.by_name.get(en, [])
        # Gated on town the same way its own R1_exact_town sibling two lines below always was: a
        # UNIQUE employer-name hit is still a wrong match when the posting's own city is known and
        # names a different registry town (TASK-81 mechanism #2; e.g. a unique-nationwide employer
        # name that also runs a Bavaria-adjacent site). Unknown city (ck falsy) is unchanged -- no
        # evidence to contradict the name match with. A known city that names no OTHER registry town
        # at all is *also* not contradicting evidence -- it can be a board/employer label ('RoMed
        # Verbund'), or a real place the registry simply has no clinic in ('Titting',
        # 'Petershausen'); only refuse when the city actually points at a DIFFERENT real registry
        # site (measured live 2026-09-21 over all 2560 open postings: the unqualified gate below
        # cost 17 R1_exact matches this way, 4 of them on a city matching no registry town at all).
        if len(c) == 1:
            own_town = city_key(c[0].get("town"))
            other_town_disagrees = en not in CITY_UNRELIABLE_EMPLOYERS and ck and not _town_match(own_town, ck) and \
                any(x["clinic_id"] != c[0]["clinic_id"] for x in self._by_town(ck))
            if not other_town_disagrees:
                return c[0]["clinic_id"], "R1_exact", 1.0
        if len(c) > 1:
            t = [x for x in c if _town_match(city_key(x.get("town")), ck)]
            if len(t) == 1: return t[0]["clinic_id"], "R1_exact_town", 0.98
        c = [] if employer_inherited else self.by_op.get(en, [])
        if len(c) == 1:
            # Same gate as R1_exact above, mirrored exactly -- a UNIQUE operator-name hit is a wrong
            # match too when the posting's own city names a DIFFERENT real registry town (TASK-153;
            # Augustinum gGmbH has exactly one Krankenhausplan site, München, so this branch used to
            # attribute every Augustinum-operator posting nationwide to it regardless of city,
            # including real postings from other Augustinum facilities -- Bischofswiesen,
            # Unterschleißheim, Oberschleißheim, Bad Tölz -- a different facility category entirely,
            # confirmed live 2026-09-24). See other_town_disagrees's own comment above for why this
            # only refuses on a proven OTHER registry site, not any known-but-unregistered city.
            own_town = city_key(c[0].get("town"))
            other_town_disagrees = en not in CITY_UNRELIABLE_EMPLOYERS and ck and not _town_match(own_town, ck) and \
                any(x["clinic_id"] != c[0]["clinic_id"] for x in self._by_town(ck))
            if not other_town_disagrees:
                return c[0]["clinic_id"], "R2_operator", 0.95
        if len(c) > 1:
            t = [x for x in c if _town_match(city_key(x.get("town")), ck)]
            if len(t) == 1: return t[0]["clinic_id"], "R2_operator_town", 0.9
            if len(t) > 1:
                # An operator tie is not necessarily a name tie: the employer text can still name
                # one of the sites far better than the others, and that beats _pick_site's bed
                # count, which is blind to what the posting actually says (confirmed live
                # 2026-09-21: kbo.de's per-posting Einsatzort block says "kbo-Kinderzentrum
                # München" -- 16211's own name -- but 16211 and 16212 share that operator, so the
                # bed count filed 13 postings under 16212 kbo-Heckscher-Klinikum München instead).
                # Same best-Jaccard idiom R3/R4 already use one rung below.
                js = sorted(((jaccard(et, x["_ntoks"]), x) for x in t), key=lambda kv: -kv[0])
                if js[0][0] - js[1][0] >= 0.1:
                    return js[0][1]["clinic_id"], "R2_operator_town_bestj", 0.85
                top = _pick_site(t, texts, note)
                if top: return top["clinic_id"], "R6_ambiguous_sites:" + ",".join(sorted(x["clinic_id"] for x in t)), 0.5
                return False
        same_town = self._by_town(ck)
        et = et - set(ck.split()); ek = kinds(employer)
        # R1/R2 above only skip the DIRECT by_name/by_op lookups on employer_inherited -- et/ek below
        # were still built from the same seed-copied text, so a generic wp_jobs board (org defaults to
        # the triggering clinic's own registry name when the page states no employer) could still
        # token-match its way back to that exact clinic via R3_tokens, just via a different rule than
        # R1_exact (confirmed live 2026-09-22, TASK-81: la-regio-kliniken.de's shared board, 2 clinics
        # in Landshut -- every posting's org was the seed clinic's own name, R1_exact correctly
        # declined on employer_inherited, R3_tokens then re-admitted the identical wrong clinic anyway
        # by token-matching the same seed text against the SAME_TOWN pool). Only blank when there is
        # more than one same-town candidate to disambiguate BETWEEN -- a single same-town candidate has
        # nothing to disambiguate from, so its token check is redundant confirmation, not circular
        # evidence, and stays allowed (TASK-62's own "a real, non-inherited city still earns the match"
        # case, tests/test_inherited_fields.py).
        if employer_inherited and len(same_town) > 1:
            et, ek = set(), set()
        if not et and not ek: return None
        # R3's same-operator bed-count guess (R6) waits for R4: the operator tokens can still name ONE
        # site outright (TASK-170: "RehaZentren der Deutschen Rentenversicherung" ties two DRV Bund houses
        # in Bad Kissingen by name, only RH2467's operator -- DRV Baden-Wuerttemberg's "Reha Zentren" --
        # carries it). Replayed over run 217's 3529 matched rows: exactly those 2 postings change.
        r6 = tied = None
        for rule, key, score in (("R3_tokens", "_ntoks", 0.8), ("R4_tokens_op", "_otoks", 0.75)):
            cands = []
            for x in same_town:
                if x[key]:
                    ok = _named(et, x[key])
                else:                                   # site name is just kind + town ("Klinikum Fürth"): need matching kind
                    ok = bool(ek & x["_kinds"]) and not et - ek  # employer carries no other distinguishing tokens
                    # A former extra fallback here (`or (ek & x["_kinds"] and len(et) <= 1)`) treated
                    # ANY single leftover employer token as safe to ignore -- meant for a stopword the
                    # tokenizer missed ('Klinikum Fürth Personalabteilung'/'AöR', both already reduce
                    # et to empty on their own, see above), but it just as readily waved through a
                    # single REAL distinguishing token that happens to not match this candidate (found
                    # live 2026-09-11: 'Klinik Reinhardshöhe GmbH', a Hesse site, false-matched to
                    # 'Klinik Bad Windsheim' purely because both towns collapse to city_key() == 'bad'
                    # and Bad Windsheim's own name/operator carry no distinguishing token at all).
                cands.append((ok, x))
            best = [x for ok, x in cands if ok]
            if len(best) == 1: return best[0]["clinic_id"], rule, score
            if len(best) > 1:
                # prefer the candidate whose own tokens are all covered (exact-er), else same-operator sites -> R6
                full = [x for x in best if x[key] <= et]
                if len(full) == 1: return full[0]["clinic_id"], rule + "_full", score - 0.05
                js = sorted(((jaccard(et, x[key]), x) for x in best), key=lambda t: -t[0])
                if js[0][0] - js[1][0] >= 0.1: return js[0][1]["clinic_id"], rule + "_bestj", score - 0.1
                # decision-4: the Bayern Krankenhausplan legitimately lists a real Plan-KH site
                # alongside a near-duplicate placeholder entry for the same building (a Vertrags-KH
                # or a beds-less satellite day-clinic, e.g. Klinikum Bamberg-Bruderwald: the real
                # 911-bed hospital (46101) plus a 0-bed Vertrags-KH twin (46170) AND a 0-bed KJP
                # day-clinic under a different operator (46110) -- all three token-tie on
                # "bruderwald"). A single candidate carrying real bed capacity among placeholders
                # is a safe, operator-independent signal; only fall through to the same-operator R6
                # tie-break below when more than one candidate actually has real capacity.
                real = [x for x in best if x.get("beds")]
                if len(real) == 1: return real[0]["clinic_id"], rule + "_realsite", score - 0.1
                ops = {employer_norm(x.get("operator") or x["name"]) for x in best}
                if len(ops) == 1:
                    top = _pick_site(best, texts, note)
                    if top: r6 = r6 or (top["clinic_id"], "R6_ambiguous_sites:" + ",".join(sorted(x["clinic_id"] for x in best)), 0.5)
                    else: tied = True
        if r6: return r6
        if tied: return False
        cands = [(overlap(et, x["_ntoks"] | x["_otoks"]), x) for x in same_town]
        best = [x for j, x in cands if j >= 0.5]
        if len(best) == 1 and len(same_town) == 1: return best[0]["clinic_id"], "R5_loose", 0.6
        return self._match_jd(description)

    def _match_jd(self, description):
        """Last content-side check before falling back to board: does exactly one clinic's own name or
        operator appear, verbatim as a token set, in the job description? Conservative on purpose --
        a JD mentioning a clinic in passing ("Kooperation mit Klinikum X") is rare enough that requiring
        a UNIQUE hit across the whole registry is safer than guessing among several mentions.

        Unlike R3/R4 (same-town candidates only), this runs against the WHOLE registry -- a name/
        operator that reduces to one generic word after town-stripping ('Artemed', 'Augenklinik') is
        common enough nationwide that treating it as a unique hit is a false-positive machine, not a
        rare coincidence (TASK-101, found the moment this rule's real output was replayed for the
        first time: Artemed Fachklinik München's own name and Augenklinik Rosenheim's both reduce to
        one token, so every OTHER site's description that mentions that word in passing -- an
        Artemed-group sibling, a city district named after an eye clinic -- won registry-wide). Same
        lesson TASK-51/decision-5 already drew for R3/R4's own now-removed single-token fallback:
        one leftover token is not evidence, at any scope, but least of all unscoped by town.

        parse_quality='partial' candidates are excluded entirely, not just gated by token count: a
        row the registry itself already flags as an imperfect Krankenhausplan-PDF extraction (garbled
        name/operator, or a town field that isn't a real town at all -- 'Co. KG', 'Kliniken', a
        person's name) breaks the SAME town-stripping this rule leans on, in the specific direction
        that manufactures false extra tokens instead of removing them (TASK-101, clinic 18872: town
        field reads 'Co. KG', so 'feldafing' never gets stripped from its operator tokens, and it
        out-competes its own clean twin 18813 for 48 real Feldafing postings). A full registry scan
        found 27 more rows with the identical shape (TASK-131) -- this is a systemic property of
        parse_quality='partial' rows, not one bad row to special-case.

        The clinic's own town has to stand in the same text. A name that is only specialty words matches
        any ad that describes the specialty: 'Klinik für Psychosomatische Medizin und Psychotherapie'
        (Parsberg) is {psychosomatische, medizin, psychotherapie}, which an AMEOS Osnabrück ad for a
        'Fachkrankenhaus für Psychiatrie, Psychotherapie und psychosomatische Medizin' contains. Name and
        town together are the facility and its place; the name alone is not (of the 15 R_jd_text links
        stored on 2026-10-01, 7 lack the town: Klinikum Seefeld for Herrsching ads, Urologische Klinik
        München-Planegg for a Straubing ad, Therapiezentrum Wolkersdorf for a Nürnberg one)."""
        if not description: return None
        text = norm_text(description[:2000])
        dt = toks(text)
        words = set(re.sub(r"[^\wäöüß ]", " ", text).split())
        def town_named(c):
            k = city_key(c.get("town"))
            # an unqualified mention ("Dillingen") names the qualified registry town ("Dillingen a.d.Donau") when no other
            # registry town starts with that word -- _town_match's own prefix rule; "Bad" alone names none of the Bad towns
            return bool(k) and (set(k.split()) <= words or
                                k.split()[0] in words and len({city_key(x.get("town")) for x in self._by_town(k.split()[0])}) == 1)
        hits =[c for c in self.clinics if c.get("parse_quality") != "partial" and town_named(c) and
                ((len(c["_ntoks"]) >= 2 and c["_ntoks"] <= dt) or (len(c["_otoks"]) >= 2 and c["_otoks"] <= dt))]
        if len(hits) == 1: return hits[0]["clinic_id"], "R_jd_text", 0.65
        return None

    def _match_board(self, pool, en, et, ck, foreign=None, texts=(), note=None):
        """The board a posting was fetched from is provenance, not a guess: the site must be one of the
        clinics sharing that board, so the candidate set is that board and nothing else. Undecidable
        within the board stays unmatched rather than falling back to a repo-wide search."""
        note = [] if note is None else note
        if not pool: return None
        # A single-clinic pool used to win outright with no city check at all -- but a group
        # portal whose registry pool collapsed to one clinic (e.g. a shared board where every
        # other member routes elsewhere) still hosts postings for OTHER towns/operators entirely
        # (confirmed live 2026-09-18: psychosomatik-diessen.de's Artemed SmartRecruiters feed, pool
        # = [Kloster Diessen], attributed 50 of its own Tutzing/Augsburg/Feldafing postings to
        # Diessen). Same rule as the name/tokens rungs below: an unknown city (ck falsy) still
        # passes through unchanged, but a known, disagreeing city refuses the match -- decision-5's
        # "no match beats a wrong match" applied to the one board rule it didn't yet cover.
        #
        # A posting that names no place (ck falsy: nothing read, or only the seed's own town copied onto
        # it) belongs to this clinic only if the board is the clinic's own single-site board -- and a board
        # is demonstrably not that when its other postings name places beyond the clinic's town (`foreign`,
        # Matcher.foreign_places, measured over the board's rows in the queue). Without it, every posting
        # of a group portal, aggregator or regional job pool whose registry pool collapsed to one clinic
        # was filed there (2026-10-01: karriere.ameos.eu 54 rows on Neuburg, krankenpflegejobs24.de 181 on
        # a Frauenklinik in Aschaffenburg, allgaeuer-jobs.de on a Kurhotel). A board whose postings never
        # name a place at all (the common own board: csj.de, klinikum-memmingen.de ...) has no counter-
        # evidence and keeps attaching.
        if len(pool) == 1:
            if ck: return (pool[0]["clinic_id"], "R0_board", 0.9) if _town_match(city_key(pool[0].get("town")), ck) else None
            if foreign:
                note.append(f"R0_board refused: the posting names no place of its own and its board names {len(foreign)} "
                            f"places beyond the town of {pool[0]['clinic_id']}, so it is not that clinic's own single-site board")
                return None
            return pool[0]["clinic_id"], "R0_board", 0.9
        # R0_board_name/_tokens match on employer text alone, which a crawler's own org-defaulting
        # bug can make IDENTICAL for every posting on a shared multi-site board regardless of the
        # real site (confirmed live 2026-09-11: karriere.ameos.eu's crawl_wp_jobs sets every row's
        # employer_name to whichever clinic seeded the crawl, so R0_board_name silently matched
        # postings for Haldensleben/Oberhausen/Hameln -- nowhere near Bavaria -- to AMEOS Klinikum
        # Neuburg just because they shared that board pool). When the posting's own city IS known,
        # require it to agree with the candidate's town before trusting name/token overlap; an
        # unknown city (ck falsy) still falls through unchanged, same as before.
        same_town_only = lambda x: not ck or _town_match(city_key(x.get("town")), ck)
        for rule, score, sel in (("R0_board_name", 0.9, lambda x: employer_norm(x["name"]) == en and same_town_only(x)),
                                 ("R0_board_town", 0.85, lambda x: ck and _town_match(city_key(x.get("town")), ck)),
                                 ("R0_board_tokens", 0.7, lambda x: x["_ntoks"] and overlap(et, x["_ntoks"]) >= 0.6 and same_town_only(x))):
            hit = [x for x in pool if sel(x)]
            if len(hit) == 1: return hit[0]["clinic_id"], rule, score
            if len(hit) > 1:
                # A tie within one rung's own signal is only safe to break by real-bed-capacity
                # (_pick_site) when the tied candidates share one normalized name or operator -- the
                # same Plan-KH/Vertrags-KH twin-site shape _match_content already resolves this way
                # (decision-4/TASK-58A). R0_board_town's own tie condition is pure city agreement, no
                # name signal at all, so without this gate a genuinely different site sharing the same
                # town (confirmed live 2026-09-22: Schön Klinik München Harlaching vs Schwabing, same
                # town, different operators) would get silently force-picked by bed count instead of
                # correctly staying unmatched.
                ops = {employer_norm(x.get("operator") or x["name"]) for x in hit}
                if len(ops) == 1:
                    top = _pick_site(hit, texts, note)
                    if top: return top["clinic_id"], rule + "_bestsite", score - 0.1
        return None


def link_postings(postings, clinics):
    """postings: dicts of employer, city, title, the crawler's stamps (employer_inherited, city_inherited) and its
    listing of the posting's locations (sites) as stored with the posting -- the same inputs
    pflege_jobs.cli._process_rows gives the Matcher, minus the board and the description."""
    m = Matcher(clinics)
    out = []
    for p in postings:
        r = m.match(p.get("employer"), p.get("city"), title=p.get("title"), employer_inherited=bool(p.get("employer_inherited")),
                    city_inherited=bool(p.get("city_inherited")), sites=p.get("sites"))
        if r:
            out.append({"posting_id": p["posting_id"], "clinic_id": r[0], "clinic_match_rule": r[1], "clinic_match_score": r[2]})
    return out

# Columns that ATS discovery owns but that a full clinics row also carries.
DISCOVERY_OWNED = ("ats_type", "careers_url")


def full_clinic_rows(clinics, live):
    """Return complete clinic rows, ready to push, with discovery-owned columns preserved.

    The ingest upsert builds its recordset from a fixed column list and assigns *every* column, so a
    payload that omits a key sends NULL for it. Pushing `{clinic_id, name, ats_type}` therefore wipes
    beds, town, Fachrichtungen and the rest — this has bitten us twice. Always send the whole row.
    """
    by_id = {c.get("clinic_id"): c for c in (live or [])}
    out = []
    for c in clinics:
        cur = by_id.get(c.get("clinic_id")) or {}
        row = {k: c.get(k, cur.get(k)) for k, _ in CLINIC_SPEC}
        row["clinic_id"] = c.get("clinic_id")
        for f in DISCOVERY_OWNED:
            row[f] = (c.get(f) or "").strip() or (cur.get(f) or "")
        out.append(row)
    return out


def merge_discovered(clinics, live, fields=DISCOVERY_OWNED):
    """Fill discovery-owned columns from the DB so a registry push is never lossy.

    The ingest upsert assigns every column of its recordset, and json_to_recordset turns a *missing*
    key into NULL just like an empty string. So both "omit the column" and "send the CSV blank"
    erase whatever ATS discovery found. The only safe push sends an explicit value: the CSV's, or
    the one already stored. Mutates and returns `clinics`.
    """
    by_id = {c.get("clinic_id"): c for c in (live or [])}
    for c in clinics:
        cur = by_id.get(c.get("clinic_id")) or {}
        for f in fields:
            c[f] = (c.get(f) or "").strip() or (cur.get(f) or "")
    return clinics
