// Thin wrappers over the FastAPI backend. All agent logic lives on the server.
async function j(res) {
  if (!res.ok) {
    let payload = null
    let msg = res.statusText
    try { payload = await res.json(); msg = payload.detail || msg } catch { /* keep the status text */ }
    const err = new Error(typeof msg === 'string' ? msg : JSON.stringify(msg))
    err.payload = payload   // {detail, code, alternatives} for a clear refusal (missing or rejected key, paid run needs confirmation, cost limit)
    err.status = res.status
    throw err
  }
  return res.json()
}
const post = (url, body) => fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }).then(j)

export const getMode = () => fetch('/api/mode').then(j)
export const startDemo = (demo_id) => post('/api/runs', { demo_id })
export const resolveDrug = (text) => post('/api/resolve', { text })
export const getModels = () => fetch('/api/models').then(j)
export const startRun = (drug, date, serious_only, opts = {}) => post('/api/runs', { drug, date, serious_only, model: opts.model, triage: opts.triage, confirm_cost: !!opts.confirmCost, fresh: !!opts.fresh })
export const getRun = (id) => fetch(`/api/runs/${id}`).then(j)
export const getSignals = (id) => fetch(`/api/runs/${id}/signals`).then(j)
export const getCard = (id, term) => fetch(`/api/runs/${id}/card?term=${encodeURIComponent(term)}`).then(j)
export const sendFeedback = (id, term, decision, reason) => post(`/api/runs/${id}/feedback`, { term, decision, reason })
export const eventsUrl = (id) => `/api/runs/${id}/events`
export const briefUrl = (id) => `/api/runs/${id}/brief.md`
