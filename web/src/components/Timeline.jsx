const AGENTS = [
  ['resolver', 'Drug Resolver'],
  ['miner', 'Miner'],
  ['triage', 'Triage'],
  ['label_checker', 'Label Checker'],
  ['skeptic', 'Skeptic'],
  ['writer', 'Writer'],
]

function Icon({ status }) {
  if (status === 'done') return <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-emerald-600 text-xs text-white" aria-label="done">✓</span>
  if (status === 'stopped' || status === 'error') return <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-rose-600 text-xs text-white" aria-label="stopped">!</span>
  if (status === 'waiting') return <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-amber-500 text-xs text-white" aria-label="waiting for the rate limit">…</span>
  if (status === 'start' || status === 'resumed') return <span className="spinner h-6 w-6 shrink-0 rounded-full border-2 border-amber-500 border-t-transparent" aria-label="running" />
  return <span className="h-6 w-6 shrink-0 rounded-full border-2 border-stone-300 dark:border-stone-600" aria-label="waiting" />
}

export default function Timeline({ events, running }) {
  const latest = {}
  for (const e of events) latest[e.step] = e
  const waiting = Object.values(latest).find((e) => e.status === 'waiting')
  return (
    <section className="rounded-lg border border-stone-300 bg-white p-4 dark:border-stone-700 dark:bg-stone-900" aria-live="polite">
      <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-stone-500">Agents</h2>
      {waiting && (
        <p data-testid="waiting" role="status" className="mb-3 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-200">
          Waiting for the free model's rate limit. The run resumes by itself; nothing is lost.
        </p>
      )}
      <ol className="space-y-3">
        {AGENTS.map(([id, label]) => {
          const e = latest[id]
          return (
            <li key={id} className="flex items-start gap-3">
              <Icon status={e?.status} />
              <div className="min-w-0">
                <p className={'text-sm font-medium ' + (e ? '' : 'text-stone-400')}>{label}</p>
                {e && <p className="break-words text-sm text-stone-600 dark:text-stone-400">{e.summary}</p>}
              </div>
            </li>
          )
        })}
      </ol>
      {running && <p className="mt-3 text-xs text-stone-500">A full run takes a few minutes: the Skeptic weighs each flagged term on three branches.</p>}
    </section>
  )
}
