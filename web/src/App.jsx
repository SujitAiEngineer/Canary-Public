import { useEffect, useRef, useState } from 'react'
import { briefUrl, eventsUrl, getMode, getModels, getRun, getSignals, resolveDrug, startDemo, startRun } from './api.js'
import RunForm from './components/RunForm.jsx'
import Timeline from './components/Timeline.jsx'
import Brief from './components/Brief.jsx'
import Sidebar from './components/Sidebar.jsx'
import FailurePanel from './components/FailurePanel.jsx'

export default function App() {
  const [phase, setPhase] = useState('idle') // idle | resolving | confirm | running | done | error
  const [message, setMessage] = useState('')
  const [suggestions, setSuggestions] = useState([])
  const [pending, setPending] = useState(null) // the form values waiting for a "did you mean?" answer
  const [runId, setRunId] = useState(null)
  const [events, setEvents] = useState([])
  const [info, setInfo] = useState(null)
  const [signals, setSignals] = useState(null)
  const [mode, setMode] = useState(null) // what this installation can do (full pipeline and/or precomputed demos)
  const [demo, setDemo] = useState(false)
  const [catalog, setCatalog] = useState(null) // model and triage choices with estimated costs (from /api/models)
  const [choice, setChoice] = useState({ model: '', triage: '', confirmCost: false })
  const [failure, setFailure] = useState(null) // {message, code, alternatives}: a refused or stopped run, with the models that can be used instead
  const source = useRef(null)
  const eventsRef = useRef([])

  useEffect(() => () => source.current?.close(), [])
  useEffect(() => { getMode().then((m) => { setMode(m); setDemo(m.default_demo) }).catch(() => {}) }, [])
  useEffect(() => { getModels().then((c) => { setCatalog(c); setChoice({ model: c.default, triage: c.triage_default, confirmCost: false, fresh: false }) }).catch(() => {}) }, [])
  const optsOf = (c) => ({ model: c.model || undefined, triage: c.triage || undefined, confirmCost: c.confirmCost, fresh: !!c.fresh })

  function useInstead(modelId) { // the user picked one of the offered models: back to the form with it selected (a paid one must be confirmed again)
    setChoice((c) => ({ ...c, model: modelId, confirmCost: false }))
    setFailure(null); setMessage(''); setPhase('idle')
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  async function launch(drug, date, serious, demoId, opts) {
    setPhase('running'); setMessage(''); setFailure(null); setEvents([]); eventsRef.current = []; setSignals(null); setInfo(null)
    try {
      const { run_id } = demoId ? await startDemo(demoId) : await startRun(drug, date, serious, opts)
      setRunId(run_id)
      const es = new EventSource(eventsUrl(run_id))
      source.current = es
      es.onmessage = (e) => { const ev = JSON.parse(e.data); eventsRef.current = [...eventsRef.current, ev]; setEvents((prev) => [...prev, ev]) }
      es.addEventListener('end', async () => {
        es.close()
        const run = await getRun(run_id)
        setInfo(run)
        if (run.status === 'ok') {
          setSignals(await getSignals(run_id))
          setPhase('done')
        } else {
          const last = eventsRef.current[eventsRef.current.length - 1]
          const msg = run.message || (last && (last.status === 'error' || last.status === 'stopped') ? last.summary : `The run stopped: ${run.status}`)
          setMessage(msg)
          setFailure({ message: msg, code: run.status, alternatives: run.alternatives || [] })
          setPhase('error')
        }
      })
    } catch (err) {
      setMessage(err.message); setFailure({ message: err.message, code: err.payload?.code, alternatives: err.payload?.alternatives || [] }); setPhase('error')
    }
  }

  async function submit({ drug, date, serious }) {
    const opts = optsOf(choice)
    setPhase('resolving'); setMessage(''); setSuggestions([]); setFailure(null)
    try {
      const r = await resolveDrug(drug)
      if (r.status === 'resolved') return launch(r.generic_name, date, serious, undefined, opts)
      if (r.status === 'needs_confirmation') {
        setSuggestions(r.suggestions); setMessage(r.message); setPending({ date, serious, opts }); setPhase('confirm'); return
      }
      setMessage(r.message); setPhase('error')
    } catch (err) {
      setMessage(err.message); setPhase('error')
    }
  }

  const busy = phase === 'resolving' || phase === 'running'
  return (
    <div className="mx-auto max-w-7xl px-4 py-6 sm:px-6">
      <header className="mb-6 flex flex-wrap items-end justify-between gap-2">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Canary <span aria-hidden>🐤</span></h1>
          <p className="text-sm text-stone-600 dark:text-stone-400">An early-warning check for drug side effects that are reported to FDA but not on the label.</p>
        </div>
        <p className="rounded-md border border-amber-300 bg-amber-50 px-3 py-1.5 text-xs font-medium text-amber-900 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-200">
          Research prototype, not medical advice
        </p>
      </header>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_340px]">
        <main className="space-y-6">
          {demo && <p className="rounded-md bg-indigo-50 px-3 py-2 text-xs text-indigo-900 dark:bg-indigo-950 dark:text-indigo-200">Demo mode: these runs were computed earlier and are replayed here. Linezolid and lithium were made in extended review mode (top 400).</p>}
          <RunForm onSubmit={submit} onDemo={(id) => launch(null, null, false, id)} busy={busy} mode={mode} demo={demo} setDemo={setDemo}
            catalog={catalog} choice={choice} setChoice={setChoice} />
          {phase === 'confirm' && (
            <section className="rounded-lg border border-stone-300 bg-white p-4 dark:border-stone-700 dark:bg-stone-900" aria-live="polite">
              <p className="mb-3 text-sm">{message}</p>
              <div className="flex flex-wrap gap-2">
                {suggestions.map((s) => (
                  <button key={s.rxcui} onClick={() => launch(s.generic_name, pending.date, pending.serious, undefined, pending.opts)}
                    className="rounded-md bg-stone-900 px-3 py-1.5 text-sm text-white hover:bg-stone-700 focus:outline-none focus:ring-2 focus:ring-amber-500 dark:bg-amber-400 dark:text-stone-900 dark:hover:bg-amber-300">
                    Yes, {s.generic_name}
                  </button>
                ))}
                <button onClick={() => setPhase('idle')} className="rounded-md border border-stone-300 px-3 py-1.5 text-sm hover:bg-stone-100 dark:border-stone-600 dark:hover:bg-stone-800">No, let me retype</button>
              </div>
            </section>
          )}
          {phase === 'error' && <FailurePanel message={message} failure={failure} onUse={useInstead} />}
          {events.length > 0 && <Timeline events={events} running={phase === 'running'} />}
          {phase === 'done' && signals && <Brief runId={runId} data={signals} />}
        </main>
        <Sidebar runId={runId} events={events} info={info} done={phase === 'done'} briefUrl={runId ? briefUrl(runId) : null} catalog={catalog} />
      </div>
    </div>
  )
}
