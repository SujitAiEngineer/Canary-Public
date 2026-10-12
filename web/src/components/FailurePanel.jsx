// A run that could not start or stopped: a clear message and, where another model can be used, buttons to switch to it.
// Codes: missing_key and key_rejected (the key in .env is absent or refused), daily_limit and rate_limited (the free model's limits),
// confirmation_required and limit (a paid run needs the cost confirmed, or would pass the hard limit).
const TITLES = {
  missing_key: 'The API key for this model is missing',
  key_rejected: 'The provider rejected the API key',
  daily_limit: "The free model's daily limit is used up",
  rate_limited: 'The free model stayed rate limited',
  confirmation_required: 'Confirm the cost to run this model',
  limit: 'This run would pass the spending limit',
}

export default function FailurePanel({ message, failure, onUse }) {
  const code = failure?.code
  const alts = (failure?.alternatives || [])
  const usable = alts.filter((a) => a.available)
  const blocked = alts.filter((a) => !a.available)
  return (
    <section role="alert" data-testid="failure" className="space-y-3 rounded-lg border border-rose-300 bg-rose-50 p-4 text-sm text-rose-900 dark:border-rose-800 dark:bg-rose-950 dark:text-rose-200">
      <p className="font-medium">{TITLES[code] || 'The run could not be completed'}</p>
      <p>{message}</p>
      {usable.length > 0 && (
        <div>
          <p className="mb-2 text-xs">Try another model:</p>
          <div className="flex flex-wrap gap-2">
            {usable.map((a) => (
              <button key={a.id} onClick={() => onUse(a.id)} data-testid={`use-${a.id}`}
                className="rounded-md bg-stone-900 px-3 py-1.5 text-sm text-white hover:bg-stone-700 focus:outline-none focus:ring-2 focus:ring-amber-500 dark:bg-amber-400 dark:text-stone-900 dark:hover:bg-amber-300">
                Use {a.label} ({a.provider_label}, {a.free ? 'free' : `about $${a.estimate_usd.toFixed(2)} per run`})
              </button>
            ))}
          </div>
        </div>
      )}
      {blocked.length > 0 && usable.length === 0 && (
        <p className="text-xs">No other model has a key in .env: {blocked.map((a) => a.label).join(', ')}. Add a key to .env, or use Demo mode, which needs none.</p>
      )}
    </section>
  )
}
