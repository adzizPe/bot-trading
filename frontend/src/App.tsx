import { useEffect, useState } from 'react'

type Health = {
  status: string
  service: string
  environment: string
  database: string
}

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL || '/api/v1'

export default function App() {
  const [health, setHealth] = useState<Health | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    fetch(`${apiBaseUrl}/health`)
      .then((response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`)
        return response.json() as Promise<Health>
      })
      .then(setHealth)
      .catch((reason: unknown) => {
        setError(reason instanceof Error ? reason.message : 'Kesalahan tidak dikenal')
      })
  }, [])

  return (
    <main className="min-h-screen bg-slate-950 px-6 py-16 text-slate-100">
      <section className="mx-auto max-w-4xl">
        <span className="rounded-full border border-amber-400/30 bg-amber-400/10 px-3 py-1 text-xs font-semibold text-amber-300">
          EDUCATION • DEMO FIRST • LIVE LOCKED
        </span>
        <h1 className="mt-6 text-4xl font-bold tracking-tight sm:text-6xl">XAU/USD Trading Bot</h1>
        <p className="mt-4 max-w-2xl text-lg text-slate-400">
          Fondasi dashboard telah aktif. Trading engine, MT5, strategi, dan risk management belum diimplementasikan.
        </p>
        <div className="mt-10 grid gap-4 sm:grid-cols-2">
          <StatusCard label="Frontend" value="RUNNING" healthy />
          <StatusCard
            label="Backend & SQLite"
            value={health ? `${health.status.toUpperCase()} • ${health.database}` : error ? `UNAVAILABLE • ${error}` : 'CHECKING'}
            healthy={Boolean(health)}
          />
        </div>
      </section>
    </main>
  )
}

function StatusCard({ label, value, healthy }: { label: string; value: string; healthy: boolean }) {
  return (
    <article className="rounded-2xl border border-slate-800 bg-slate-900 p-6 shadow-xl shadow-black/20">
      <p className="text-sm text-slate-400">{label}</p>
      <p className={`mt-2 font-mono text-sm font-semibold ${healthy ? 'text-emerald-400' : 'text-amber-300'}`}>{value}</p>
    </article>
  )
}
