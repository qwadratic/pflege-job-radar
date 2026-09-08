export const meta = {
  name: 'judge-runner',
  description: 'Find N verified bugs (multi-lens finders → K independent judges, majority vote), then propose up to M merge requests, one branch each, tests green',
  whenToUse: 'Scheduled quality loop for pflege-board: run phase=find in the morning, phase=propose in the afternoon; args configure counts, judges and lenses',
  phases: [
    { title: 'Find', detail: 'lens finders in parallel, loop until N verified' },
    { title: 'Judge', detail: 'K judges per finding, distinct lenses, majority' },
    { title: 'Propose', detail: 'top M verified bugs → worktree fix → tests → branch + MR' },
  ],
}

// ---- config (args override .judge/config.json which the first agent reads) -------------------
const A = Object.assign({ phase: 'both', bugs: 5, mrs: 2, judges: 3, max_rounds: 3, model: null, dry: false,
  lenses: ['correctness', 'security', 'data-quality', 'performance', 'ux-copy'],
  judge_lenses: ['correctness', 'security', 'reproduction'] }, args || {})

const STYLE = `
## Working style (mandatory for every agent in this run)
Caveman ultra for prose: terse, no filler, no hedging, abbreviations for prose words only (fn/impl/config/req/res),
never abbreviate code symbols, error strings or paths; code blocks and commands untouched and exact.
Ponytail lite for code: the simplest, shortest change that actually works; stdlib first; no unrequested abstractions,
no new deps, no refactors beyond the fix; NEVER cut validation, error handling, security, tests or accessibility.
Repo: /home/exedev/repo (venv .venv; env: set -a; . ./.env; set +a). Never restart the pflege-web service, never
bind port 8501, never commit to main, never push main, never call Firecrawl/Exa/the LLM gateway. Reads of the live DB
are free and keyless: curl -s -H 'Accept-Profile: pflege_jobs' 'https://supabase.int.exe.xyz/rest/v1/<table>?...'.
'.venv/bin/pytest -q' is the contract (must stay green).
`

const FINDING = {
  type: 'object', additionalProperties: false,
  required: ['title', 'file', 'line', 'severity', 'category', 'evidence', 'repro', 'proposed_fix', 'fix_effort'],
  properties: {
    title: { type: 'string' }, file: { type: 'string' }, line: { type: 'integer' },
    severity: { type: 'string', enum: ['critical', 'high', 'medium', 'low'] },
    category: { type: 'string' }, evidence: { type: 'string', description: 'What you ran/read that proves it (command + output, or file:line quote).' },
    repro: { type: 'string', description: 'Exact steps or command that reproduces it.' },
    proposed_fix: { type: 'string', description: 'The smallest change that fixes it, ponytail style.' },
    fix_effort: { type: 'string', enum: ['S', 'M', 'L'] },
  },
}
const FINDINGS = { type: 'object', additionalProperties: false, required: ['day', 'findings'],
  properties: { day: { type: 'string', description: 'UTC date YYYY-MM-DD from `date -u +%F`' }, findings: { type: 'array', items: FINDING } } }
const VERDICT = { type: 'object', additionalProperties: false, required: ['real', 'confidence', 'why'],
  properties: { real: { type: 'boolean' }, confidence: { type: 'number' }, why: { type: 'string' } } }
const MR = { type: 'object', additionalProperties: false, required: ['branch', 'title', 'status', 'tests', 'summary'],
  properties: { branch: { type: 'string' }, title: { type: 'string' }, status: { type: 'string', enum: ['pushed', 'pr_opened', 'mr_file', 'skipped', 'failed'] },
    tests: { type: 'string' }, summary: { type: 'string' }, url: { type: 'string' } } }

const key = f => `${f.file}:${Math.floor(f.line / 20)}:${f.title.toLowerCase().slice(0, 40)}`
const verified = [], seen = new Set()
let day = A.day || null

if (A.phase === 'find' || A.phase === 'both') {
  let dry = 0, round = 0
  while (verified.length < A.bugs && dry < 2 && round < A.max_rounds) {
    round++
    phase('Find')
    const batches = await parallel(A.lenses.map(lens => () => agent(`${STYLE}
## You are a bug finder. Lens: ${lens}. Round ${round}. Already found (skip these): ${[...seen].join('; ') || 'none'}
Read .judge/config.json (if present) for focus paths and exclusions. Hunt REAL, reproducible defects in this repo through
the ${lens} lens only: correctness = wrong results, crashes, races, silent data loss (adapters, classify, inbox drain,
resolve/merge, billing math); security = injection in PostgREST filters/SQL/f-strings, secrets in responses, unauth'd
write endpoints, path handling, webhook auth; data-quality = postings over-merged/duplicated/mis-linked, wrong role_class,
stale expiry, unattributed clinics (query the live DB); performance = O(n^2) on 1000-row paths, unbounded limits,
repeated full-table reads per request; ux-copy = broken i18n keys, DE/EN mismatches, misleading labels, mobile overflow.
Rules: every finding needs evidence you actually produced now (a command and its output, or a quoted file:line) and a
repro. No style nits, no hypotheticals, no 'could be improved'. Return up to 4 findings; return 0 rather than pad. Run
'date -u +%F' and put it in day.`, { label: `find:${lens}`, phase: 'Find', schema: FINDINGS, effort: 'high', ...(A.model ? { model: A.model } : {}) })))
    const fresh = []
    for (const b of batches.filter(Boolean)) {
      if (!day && b.day) day = b.day
      for (const f of b.findings || []) { const k = key(f); if (!seen.has(k)) { seen.add(k); fresh.push(f) } }
    }
    log(`round ${round}: ${fresh.length} new candidate(s), ${verified.length}/${A.bugs} verified so far`)
    if (!fresh.length) { dry++; continue }
    dry = 0
    phase('Judge')
    const judged = await parallel(fresh.map(f => () =>
      parallel(A.judge_lenses.slice(0, A.judges).map((lens, i) => () => agent(`${STYLE}
## You are judge ${i + 1} of ${A.judges}. Lens: ${lens}. Default to real=false unless you can confirm it yourself.
Finding: ${JSON.stringify(f)}
Independently re-check it NOW: correctness = read the code path and reason through inputs; security = try to exploit
or prove it unreachable; reproduction = run the repro command and paste what happened. A finding whose evidence you
cannot reproduce or whose 'fix' would break tests/security is NOT real. Answer real/confidence/why in 3 lines max.`,
        { label: `judge:${lens}:${f.file.split('/').pop()}`, phase: 'Judge', schema: VERDICT, effort: 'medium', ...(A.model ? { model: A.model } : {}) })))
        .then(vs => ({ f, votes: vs.filter(Boolean), yes: vs.filter(Boolean).filter(v => v.real).length }))))
    for (const j of judged.filter(Boolean)) {
      const need = Math.floor(j.votes.length / 2) + 1
      if (j.votes.length && j.yes >= need) verified.push({ ...j.f, votes: j.votes, yes: j.yes, of: j.votes.length })
      else log(`rejected (${j.yes}/${j.votes.length}): ${j.f.title}`)
      if (verified.length >= A.bugs) break
    }
  }
  const rank = { critical: 0, high: 1, medium: 2, low: 3 }, eff = { S: 0, M: 1, L: 2 }
  verified.sort((a, b) => (rank[a.severity] - rank[b.severity]) || (eff[a.fix_effort] - eff[b.fix_effort]) || (b.yes - a.yes))
  await agent(`${STYLE}
## Persist today's verified findings
Write this JSON to /home/exedev/repo/.judge/queue/${day || 'unknown'}.json (mkdir -p .judge/queue): ${JSON.stringify({ day, bugs: verified }, null, 1)}
Then append one line per finding to /home/exedev/repo/.judge/log.md: '- ${day}: <severity> <file>:<line> <title> (<yes>/<of> judges)'.
Do not commit. Reply with the file path and the count.`, { label: 'persist', phase: 'Judge', effort: 'low' })
  log(`verified ${verified.length}/${A.bugs} after ${round} round(s)`)
}

let proposed = []
if (A.phase === 'propose' || A.phase === 'both') {
  phase('Propose')
  const picks = await agent(`${STYLE}
## Pick today's merge-request candidates
Read the newest file in /home/exedev/repo/.judge/queue/ (or /home/exedev/repo/.judge/queue/${day || ''}.json if it exists)
and /home/exedev/repo/.judge/mr/ (branches already proposed; skip their findings). Return the top ${A.mrs} findings by
severity then smallest fix_effort then judge votes, as {day, findings:[...]} with the SAME finding objects, untouched.`,
    { label: 'pick', phase: 'Propose', schema: FINDINGS, effort: 'low' })
  const todo = (picks && picks.findings || []).slice(0, A.mrs)
  if (!day && picks && picks.day) day = picks.day
  log(`${todo.length} merge request(s) to propose`)
  proposed = (await parallel(todo.map((f, i) => () => agent(`${STYLE}
## Propose merge request ${i + 1}/${todo.length} for this verified bug
${JSON.stringify(f, null, 1)}
You are in an isolated git worktree. Steps: 1) git checkout -b judge/${day || 'day'}-${(f.title || 'fix').toLowerCase().replace(/[^a-z0-9]+/g, '-').slice(0, 40)};
2) implement the smallest correct fix (ponytail lite) + a regression test that fails before and passes after;
3) '.venv/bin/pytest -q' must be green (if the worktree lacks .venv, use /home/exedev/repo/.venv/bin/pytest);
4) commit with a one-line subject + 3-line body (what/why/how verified), Co-Authored-By: Claude <noreply@anthropic.com>;
5) run: /home/exedev/repo/.venv/bin/python /home/exedev/repo/tools/judge_propose.py --branch <branch> --title "<subject>"
   --body-file <(printf '%s' "<body>") ${A.dry ? '--dry-run' : ''}  -- it pushes the branch and opens a PR with gh when
   authenticated, otherwise writes .judge/mr/<branch>.md with the compare link and e-mails the owner. Never push main.
Report branch, title, status, tests, summary, url.`,
    { label: `mr:${i + 1}`, phase: 'Propose', schema: MR, effort: 'high', isolation: 'worktree', ...(A.model ? { model: A.model } : {}) })))).filter(Boolean)
}

return { day, config: A, verified: verified.map(v => ({ title: v.title, file: v.file, line: v.line, severity: v.severity, votes: `${v.yes}/${v.of}` })), proposed }
