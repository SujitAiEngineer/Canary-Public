import { useState } from 'react'

const field = 'w-full rounded-md border border-stone-300 bg-white px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-amber-500 dark:border-stone-600 dark:bg-stone-900'
const primary = 'rounded-md bg-stone-900 px-4 py-2 text-sm font-medium text-white hover:bg-stone-700 focus:outline-none focus:ring-2 focus:ring-amber-500 disabled:opacity-40 dark:bg-amber-400 dark:text-stone-900 dark:hover:bg-amber-300'
const badge = 'rounded px-1.5 py-0.5 text-xs font-medium'

// One model for all agents. Each option shows its provider, its model ID and what one run is estimated to cost; the free model says "Free" and carries the
// label it earned on the gold set (read by the server from model_portability.md). A paid model needs an explicit confirmation of the estimate.
function ModelPicker({ catalog, choice, setChoice, busy }) {
  const model = choice.model || catalog.default
  const picked = catalog.models.find((m) => m.id === model)
  return (
    <fieldset className="sm:col-span-3" disabled={busy}>
      <legend className="mb-1 text-sm font-medium">Model <span className="font-normal text-stone-500">(one model for all agents)</span></legend>
      <div className="grid gap-2 sm:grid-cols-2" role="radiogroup" aria-label="Model">
        {catalog.models.map((m) => {
          const on = m.id === model
          return (
            <label key={m.id} data-testid={`model-${m.id}`}
              className={'flex cursor-pointer gap-3 rounded-md border p-3 text-sm ' + (on ? 'border-amber-500 bg-amber-50 dark:bg-amber-950/40' : 'border-stone-300 dark:border-stone-600') + (m.available ? '' : ' opacity-60')}>
              <input type="radio" name="model" value={m.id} checked={on} disabled={!m.available || busy} className="mt-1 h-4 w-4 accent-amber-600"
                onChange={() => setChoice({ ...choice, model: m.id, confirmCost: false })} />
              <span className="min-w-0">
                <span className="flex flex-wrap items-center gap-2">
                  <span className="font-medium">{m.label}</span>
                  <span className={badge + ' bg-stone-100 text-stone-700 dark:bg-stone-800 dark:text-stone-300'}>{m.provider_label}</span>
                  {m.free
                    ? <span className={badge + ' bg-emerald-100 text-emerald-900 dark:bg-emerald-900/50 dark:text-emerald-200'}>Free</span>
                    : <span className={badge + ' bg-amber-100 text-amber-900 dark:bg-amber-900/50 dark:text-amber-200'}>about ${m.estimate_usd.toFixed(2)} per run</span>}
                </span>
                <code className="mt-0.5 block break-all text-xs text-stone-500">{m.model_id}</code>
                {m.free && m.note && <span className="mt-1 block text-xs text-stone-600 dark:text-stone-400">{m.note}. <span className="text-stone-500">{m.caveat}.</span></span>}
                {!m.free && <span className="mt-1 block text-xs text-stone-500">estimated cost per run, nothing cached; up to ${m.max_usd.toFixed(2)}</span>}
                {!m.available && <span className="mt-1 block text-xs text-rose-700 dark:text-rose-300">{m.note_unavailable}. Pick another model, or add the key to .env.</span>}
              </span>
            </label>
          )
        })}
      </div>
      {picked?.paid && (
        <label className="mt-3 flex flex-wrap items-center gap-2 text-sm">
          <input type="checkbox" checked={choice.confirmCost} onChange={(e) => setChoice({ ...choice, confirmCost: e.target.checked })} className="h-4 w-4 accent-amber-600" />
          <span><b>Confirm the cost:</b> about ${picked.estimate_usd.toFixed(2)} for this run (up to ${picked.max_usd.toFixed(2)}).</span>
          <span className="text-stone-500 dark:text-stone-400">Hard limit ${catalog.ledger_limit_usd.toFixed(2)} for all paid runs; spent so far ${catalog.ledger_spent_usd.toFixed(2)}.</span>
        </label>
      )}
    </fieldset>
  )
}

function TriagePicker({ catalog, choice, setChoice, busy }) {
  const triage = choice.triage || catalog.triage_default
  return (
    <fieldset className="sm:col-span-3" disabled={busy}>
      <legend className="mb-1 text-sm font-medium">Triage <span className="font-normal text-stone-500">(which flagged terms reach the Label Checker)</span></legend>
      <div className="grid gap-2 sm:grid-cols-2" role="radiogroup" aria-label="Triage">
        {catalog.triage_options.map((o) => (
          <label key={o.id} data-testid={`triage-${o.id}`}
            className={'flex cursor-pointer gap-3 rounded-md border p-3 text-sm ' + (o.id === triage ? 'border-amber-500 bg-amber-50 dark:bg-amber-950/40' : 'border-stone-300 dark:border-stone-600')}>
            <input type="radio" name="triage" value={o.id} checked={o.id === triage} className="mt-1 h-4 w-4 accent-amber-600" onChange={() => setChoice({ ...choice, triage: o.id })} />
            <span>
              <span className="font-medium">{o.label}</span>{o.default && <span className="ml-2 text-xs text-stone-500">default</span>}
              <span className="mt-0.5 block text-xs text-stone-600 dark:text-stone-400">{o.explanation}</span>
            </span>
          </label>
        ))}
      </div>
    </fieldset>
  )
}

export default function RunForm({ onSubmit, onDemo, busy, mode, demo, setDemo, catalog, choice, setChoice }) {
  const [drug, setDrug] = useState('')
  const [date, setDate] = useState('2019-12-31')
  const [serious, setSerious] = useState(false)
  const [demoId, setDemoId] = useState('')
  const today = new Date().toISOString().slice(0, 10)
  const demos = mode?.demos || []
  const chosen = demoId || demos[0]?.id || ''
  const model = choice.model || catalog?.default
  const picked = catalog?.models.find((m) => m.id === model)
  const blocked = !!catalog && !!picked && (!picked.available || (picked.paid && !choice.confirmCost))

  return (
    <section className="space-y-3 rounded-lg border border-stone-300 bg-white p-4 dark:border-stone-700 dark:bg-stone-900">
      {mode && (
        <label className="flex flex-wrap items-center gap-2 text-sm">
          <input type="checkbox" checked={demo} disabled={busy || !mode.full_available || demos.length === 0} onChange={(e) => setDemo(e.target.checked)} className="h-4 w-4 accent-indigo-600" />
          <span className="font-medium">Demo mode</span>
          <span className="text-stone-500 dark:text-stone-400">
            {mode.full_available ? '· replay a precomputed run: no FAERS data or API key needed' : '· the full pipeline is not available here (no FAERS data or API key), so only the precomputed demos run'}
          </span>
        </label>
      )}

      {demo ? (
        <form onSubmit={(e) => { e.preventDefault(); if (chosen) onDemo(chosen) }} className="grid gap-4 sm:grid-cols-[1fr_auto] sm:items-end">
          <label className="block text-sm font-medium">Precomputed run
            <select className={field + ' mt-1'} value={chosen} onChange={(e) => setDemoId(e.target.value)} disabled={busy}>
              {demos.map((d) => <option key={d.id} value={d.id}>{d.title}</option>)}
            </select>
          </label>
          <button disabled={busy || !chosen} className={primary}>{busy ? 'Replaying…' : 'Replay demo'}</button>
        </form>
      ) : (
        <form onSubmit={(e) => { e.preventDefault(); if (drug.trim()) onSubmit({ drug: drug.trim(), date, serious }) }}
          className="grid gap-4 sm:grid-cols-[1fr_180px_auto] sm:items-end">
          <label className="block text-sm font-medium">Drug name
            <input className={field + ' mt-1'} value={drug} onChange={(e) => setDrug(e.target.value)} placeholder="e.g. Lipitor, Zyvox" disabled={busy} autoFocus />
          </label>
          <label className="block text-sm font-medium">Time machine date
            <input type="date" className={field + ' mt-1'} value={date} max={today} onChange={(e) => setDate(e.target.value)} disabled={busy} />
          </label>
          <button disabled={busy || !drug.trim() || blocked} className={primary}>{busy ? 'Running…' : 'Run Canary'}</button>
          {catalog && <ModelPicker catalog={catalog} choice={choice} setChoice={setChoice} busy={busy} />}
          {catalog && <TriagePicker catalog={catalog} choice={choice} setChoice={setChoice} busy={busy} />}
          <label className="flex items-center gap-2 text-sm sm:col-span-3">
            <input type="checkbox" checked={serious} onChange={(e) => setSerious(e.target.checked)} disabled={busy} className="h-4 w-4 accent-amber-600" />
            Serious reports only
            <span className="text-stone-500 dark:text-stone-400">· Canary sees only reports received before the date.</span>
          </label>
          <label className="flex items-center gap-2 text-sm sm:col-span-3">
            <input type="checkbox" data-testid="fresh-run" checked={!!choice.fresh} onChange={(e) => setChoice({ ...choice, fresh: e.target.checked })} disabled={busy} className="h-4 w-4 accent-amber-600" />
            Fresh run
            <span className="text-stone-500 dark:text-stone-400">· ignore saved answers and make real model calls and live MeSH lookups, even for a drug and date run before (new answers are still saved; a paid model costs money).</span>
          </label>
        </form>
      )}
    </section>
  )
}
