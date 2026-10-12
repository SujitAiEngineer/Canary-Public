export default function Sidebar({ runId, events, info, done, briefUrl, catalog }) {
  const last = events[events.length - 1]
  const runSpend = info?.run_spend ?? last?.run_spend ?? 0
  const ledger = info?.ledger_total ?? last?.ledger_total ?? 0
  const budget = info?.budget ?? catalog?.ledger_limit_usd ?? 6.0   // the hard limit for paid runs
  return (
    <aside className="space-y-4 lg:sticky lg:top-6 lg:self-start">
      <section className="rounded-lg border border-stone-300 bg-white p-4 dark:border-stone-700 dark:bg-stone-900">
        <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-stone-500">Spend</h2>
        {info?.demo ? (
          <><p className="text-2xl font-semibold tabular-nums">${(info.run_spend ?? 0).toFixed(2)}</p>
            <p className="text-xs text-stone-500">what this run cost when it was computed. Replaying costs nothing.</p></>
        ) : (
          <><p className="text-2xl font-semibold tabular-nums">${runSpend.toFixed(3)}</p>
            <p className="text-xs text-stone-500">this run · ${ledger.toFixed(2)} of ${budget.toFixed(2)} paid-run budget used</p>
            <div className="mt-2 h-1.5 overflow-hidden rounded bg-stone-200 dark:bg-stone-700" role="progressbar" aria-valuenow={ledger} aria-valuemax={budget}>
              <div className="h-full bg-amber-500" style={{ width: `${Math.min(100, (100 * ledger) / budget)}%` }} />
            </div></>
        )}
      </section>
      <section className="rounded-lg border border-stone-300 bg-white p-4 dark:border-stone-700 dark:bg-stone-900">
        <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-stone-500">Trace</h2>
        {runId && <p className="mb-2 break-all text-xs text-stone-500">run {runId}</p>}
        {events.length === 0 ? <p className="text-sm text-stone-500">Nothing yet.</p> : (
          <ul className="max-h-80 space-y-1.5 overflow-auto text-xs">
            {events.map((e, i) => (
              <li key={i} className="grid grid-cols-[56px_1fr] gap-2">
                <span className="tabular-nums text-stone-400">{e.ts}</span>
                <span><b className="font-medium">{e.step}</b> <span className="text-stone-500">{e.status}</span></span>
              </li>
            ))}
          </ul>
        )}
        {done && briefUrl && <a href={briefUrl} target="_blank" rel="noreferrer" className="mt-3 inline-block text-sm text-amber-700 underline dark:text-amber-300">Download brief (Markdown)</a>}
      </section>
    </aside>
  )
}
