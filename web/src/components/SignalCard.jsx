import { useState } from 'react'
import { sendFeedback } from '../api.js'

const title = (t) => t.toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase())
const BRANCHES = [['real_effect', 'Real effect'], ['reporting_spike', 'Reporting spike'], ['confounding', 'Confounding']]

function Chip({ children, tone = 'stone' }) {
  const tones = {
    stone: 'bg-stone-100 text-stone-700 dark:bg-stone-800 dark:text-stone-300',
    amber: 'bg-amber-100 text-amber-900 dark:bg-amber-900/50 dark:text-amber-200',
    rose: 'bg-rose-100 text-rose-900 dark:bg-rose-900/50 dark:text-rose-200',
  }
  return <span className={'rounded px-2 py-0.5 text-xs font-medium ' + tones[tone]}>{children}</span>
}

function Cite({ id, text }) {
  const [open, setOpen] = useState(false)
  return (
    <span>
      <button type="button" onClick={() => setOpen(!open)} title={text} aria-expanded={open}
        className="mx-0.5 rounded bg-amber-100 px-1 text-xs font-medium text-amber-900 hover:bg-amber-200 focus:outline-none focus:ring-2 focus:ring-amber-500 dark:bg-amber-900/50 dark:text-amber-200">{id}</button>
      {open && <span className="my-1 block rounded border border-stone-200 bg-stone-50 p-2 text-xs text-stone-700 dark:border-stone-700 dark:bg-stone-950 dark:text-stone-300"><b>{id}</b> {text}</span>}
    </span>
  )
}

export default function SignalCard({ runId, s }) {
  const [fb, setFb] = useState(s.feedback)
  const [rejecting, setRejecting] = useState(false)
  const [reason, setReason] = useState('')
  const [err, setErr] = useState('')

  async function save(decision, why = '') {
    setErr('')
    try {
      await sendFeedback(runId, s.term, decision, why)
      setFb({ decision, reason: why }); setRejecting(false); setReason('')
    } catch (e) { setErr(e.message) }
  }

  const lab = s.label
  const labelTone = lab.decision === 'partial' ? 'amber' : 'rose'
  return (
    <article className="rounded-lg border border-stone-300 bg-white p-4 dark:border-stone-700 dark:bg-stone-900">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <h3 className="text-base font-semibold">{title(s.term)}{s.featured && <span className="ml-2 rounded bg-indigo-100 px-2 py-0.5 align-middle text-xs font-medium text-indigo-900 dark:bg-indigo-900/50 dark:text-indigo-200">demo highlight</span>}</h3>
        <div className="flex flex-wrap gap-1.5">
          {s.rank && <Chip>#{s.rank}</Chip>}
          <Chip>{s.stats.a} reports</Chip>
          <Chip>PRR {s.stats.PRR.toFixed(1)}</Chip>
          <Chip>χ² {s.stats.chi2.toFixed(0)}</Chip>
          <Chip tone={labelTone}>{lab.decision === 'partial' ? 'partially labeled' : 'not on label'}</Chip>
          {s.flag && <Chip tone="amber">{s.flag}</Chip>}
        </div>
      </div>
      {lab.evidence && <p className="mt-2 text-xs italic text-stone-500">Label of {lab.date}: “{lab.evidence}”</p>}

      <div className="mt-3 grid gap-2 sm:grid-cols-3">
        {BRANCHES.map(([k, name]) => (
          <div key={k} title={s.branches[k].reason}>
            <div className="flex justify-between text-xs"><span className={s.winner === k ? 'font-semibold' : 'text-stone-500'}>{name}{s.winner === k ? ' (winner)' : ''}</span><span className="tabular-nums">{s.branches[k].score.toFixed(2)}</span></div>
            <div className="mt-1 h-1.5 overflow-hidden rounded bg-stone-200 dark:bg-stone-700"><div className={'h-full ' + (s.winner === k ? 'bg-amber-500' : 'bg-stone-400')} style={{ width: `${s.branches[k].score * 100}%` }} /></div>
          </div>
        ))}
      </div>

      {s.claims.length === 0 && <p className="mt-3 text-sm text-stone-500">No summary was written for this term (only the strongest terms are written up). The numbers, the Skeptic's reasons and all the evidence are below.</p>}
      <details className="mt-3 text-sm">
        <summary className="cursor-pointer font-medium">The Skeptic's reasons</summary>
        <ul className="mt-2 space-y-1.5">
          {BRANCHES.map(([k, name]) => <li key={k}><b>{name}</b> ({s.branches[k].score.toFixed(2)}): {s.branches[k].reason}</li>)}
        </ul>
      </details>
      <details className="mt-2 text-sm">
        <summary className="cursor-pointer font-medium">All evidence ({Object.keys(s.evidence).length} items)</summary>
        <ul className="mt-2 space-y-1 text-xs text-stone-700 dark:text-stone-300">
          {Object.entries(s.evidence).map(([id, tx]) => <li key={id}><b>{id}</b> {tx}</li>)}
        </ul>
      </details>
      <ul className="mt-3 list-disc space-y-1.5 pl-5 text-sm leading-6">
        {s.claims.map((c, i) => (
          <li key={i}>{c.text}{c.cited_ids.map((id) => <Cite key={id} id={id} text={s.evidence[id]} />)}
            {c.label_quote && <span className="block text-xs italic text-stone-500">Label quote: “{c.label_quote}”</span>}</li>
        ))}
      </ul>

      {s.decision === 'dismissed' && (
        <p className="mt-3 rounded-md bg-stone-100 p-2 text-sm dark:bg-stone-800"><b>Dismissed by the Skeptic</b> ({s.winner.replace('_', ' ')}): {s.reason}</p>
      )}
      {s.decision !== 'dismissed' && <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-stone-200 pt-3 dark:border-stone-700">
        <button onClick={() => save('approve')} className="rounded-md bg-emerald-700 px-3 py-1.5 text-sm text-white hover:bg-emerald-600 focus:outline-none focus:ring-2 focus:ring-emerald-400">Approve</button>
        <button onClick={() => setRejecting(!rejecting)} className="rounded-md border border-rose-400 px-3 py-1.5 text-sm text-rose-700 hover:bg-rose-50 focus:outline-none focus:ring-2 focus:ring-rose-400 dark:text-rose-300 dark:hover:bg-rose-950">Reject</button>
        {fb && <span className="text-sm" aria-live="polite">{fb.decision === 'approve' ? '✓ Approved' : `✗ Rejected: ${fb.reason}`}</span>}
      </div>}
      {rejecting && s.decision !== 'dismissed' && (
        <div className="mt-2 flex flex-wrap gap-2">
          <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Reason for rejecting (required)" aria-label="Reason for rejecting"
            className="min-w-0 flex-1 rounded-md border border-stone-300 bg-white px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-rose-400 dark:border-stone-600 dark:bg-stone-950" />
          <button disabled={!reason.trim()} onClick={() => save('reject', reason)} className="rounded-md bg-rose-700 px-3 py-1.5 text-sm text-white hover:bg-rose-600 disabled:opacity-40">Save rejection</button>
        </div>
      )}
      {err && <p role="alert" className="mt-2 text-sm text-rose-700">{err}</p>}
    </article>
  )
}
