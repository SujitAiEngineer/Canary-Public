import { useState } from 'react'
import { getCard } from '../api.js'
import SignalCard from './SignalCard.jsx'

const title = (t) => t.toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase())
const provider = (m) => (m.startsWith('openrouter:') ? 'OpenRouter' : 'Anthropic')
const modelId = (m) => (m.startsWith('openrouter:') ? m.slice('openrouter:'.length) : m)

// Which model and which triage produced this brief, and the notice that it is not medical advice (every brief carries both).
function Provenance({ p }) {
  if (!p) return null
  const same = p.pipeline_model === p.brief_model
  return (
    <div data-testid="provenance" className="mt-2 rounded-md bg-stone-50 px-3 py-2 text-xs text-stone-700 dark:bg-stone-800 dark:text-stone-300">
      <p>
        <b>Produced by</b> model <code>{modelId(p.pipeline_model)}</code> ({provider(p.pipeline_model)})
        {same ? '' : <> for the Label Checker and Skeptic, and <code>{modelId(p.brief_model)}</code> ({provider(p.brief_model)}) for the brief</>}
        ; triage: <b>{p.triage_label}</b>{p.triage_inferred ? ' (this run predates the triage choice; it used the locked triage)' : ''}{p.fresh ? '; fresh run (answer cache ignored)' : ''}.
      </p>
      <p className="mt-0.5 text-stone-500">{p.triage_explanation}</p>
      <p className="mt-1 font-medium text-amber-800 dark:text-amber-300">{p.notice}</p>
    </div>
  )
}

function Fold({ label, children, open }) {
  return (
    <details open={open} className="rounded-lg border border-stone-300 bg-white p-4 dark:border-stone-700 dark:bg-stone-900">
      <summary className="cursor-pointer text-sm font-medium">{label}</summary>
      <div className="mt-3 text-sm">{children}</div>
    </details>
  )
}

// The other recommended terms: a filterable list; click a term to open its full card (numbers, label, the Skeptic's reasons, evidence).
function RecommendedList({ runId, items }) {
  const [q, setQ] = useState('')
  const [open, setOpen] = useState(null)
  const [card, setCard] = useState(null)
  const [err, setErr] = useState('')
  const shown = items.filter((r) => r.term.toLowerCase().includes(q.toLowerCase()))

  async function pick(term) {
    if (open === term) { setOpen(null); setCard(null); return }
    setOpen(term); setCard(null); setErr('')
    try { setCard(await getCard(runId, term)) } catch (e) { setErr(e.message) }
  }

  return (
    <div>
      <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Filter terms" aria-label="Filter recommended terms"
        className="mb-3 w-full rounded-md border border-stone-300 bg-white px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-amber-500 dark:border-stone-600 dark:bg-stone-950" />
      <ul className="max-h-96 divide-y divide-stone-200 overflow-auto dark:divide-stone-700">
        {shown.map((r) => (
          <li key={r.term}>
            <button onClick={() => pick(r.term)} aria-expanded={open === r.term}
              className="flex w-full items-center justify-between gap-2 px-1 py-2 text-left hover:bg-stone-50 focus:outline-none focus:ring-2 focus:ring-amber-500 dark:hover:bg-stone-800">
              <span>{r.rank && <span className="mr-2 tabular-nums text-xs text-stone-400">#{r.rank}</span>}{title(r.term)}
                {r.feedback && <span className="ml-2 text-xs text-stone-500">{r.feedback.decision === 'approve' ? '✓' : '✗'}</span>}</span>
              <span className="shrink-0 text-xs text-stone-500">{r.a} reports · PRR {r.PRR.toFixed(1)} · χ² {r.chi2.toFixed(0)}</span>
            </button>
            {open === r.term && (
              <div className="py-2">
                {err && <p role="alert" className="text-sm text-rose-700">{err}</p>}
                {!card && !err && <p className="text-sm text-stone-500">Loading…</p>}
                {card && <SignalCard runId={runId} s={card} />}
              </div>
            )}
          </li>
        ))}
        {shown.length === 0 && <li className="py-2 text-stone-500">No term matches.</li>}
      </ul>
    </div>
  )
}

export default function Brief({ runId, data }) {
  const c = data.counts
  const nonstandard = data.mode && !data.mode.startsWith('standard')
  return (
    <section className="space-y-4" aria-label="Brief">
      <div className="rounded-lg border border-stone-300 bg-white p-4 dark:border-stone-700 dark:bg-stone-900">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="text-lg font-semibold">Brief: {data.drug}, data before {data.date}</h2>
          {nonstandard && <span className="rounded bg-indigo-100 px-2 py-0.5 text-xs font-medium text-indigo-900 dark:bg-indigo-900/50 dark:text-indigo-200">{data.mode}</span>}
        </div>
        <p className="mt-1 text-sm text-stone-600 dark:text-stone-400">
          The Skeptic weighed {c.examined} terms: <b>{c.recommended}</b> recommended for human review, {c.dismissed} dismissed with a cited reason.
          The {c.written} below are written up in order of statistical strength; any recommended term can be opened from the list further down.
          Every claim cites the evidence behind it; hover or tap a tag to read it.
        </p>
        {nonstandard && <p className="mt-2 text-xs text-stone-500">Extended review is a demo-only setting: the Label Checker and Skeptic examine more terms than the standard top 150. The evaluation does not use it.</p>}
        <Provenance p={data.provenance} />
      </div>
      {data.signals.map((s) => <SignalCard key={s.term} runId={runId} s={s} />)}
      {(data.dismissed_highlights || []).map((s) => (
        <div key={s.term}>
          <p className="mb-1 text-xs text-stone-500">Demo highlight: the Skeptic examined this term and dismissed it. It is shown here so the demo can be read honestly; it is not among the recommended terms.</p>
          <SignalCard runId={runId} s={s} />
        </div>
      ))}
      {data.also_recommended.length > 0 && (
        <Fold label={`All other recommended terms (${data.also_recommended.length}): open any card`}>
          <RecommendedList runId={runId} items={data.also_recommended} />
        </Fold>
      )}
      {data.dismissed.length > 0 && (
        <Fold label={`Dismissed by the Skeptic (${data.dismissed.length}): open any card`}>
          <RecommendedList runId={runId} items={data.dismissed} />
        </Fold>
      )}
    </section>
  )
}
