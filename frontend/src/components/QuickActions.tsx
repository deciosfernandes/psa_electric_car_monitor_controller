import { useState, type ReactNode } from 'react'
import type { CarSnapshot } from '../api'

interface Props {
  car: CarSnapshot | null
  run: (key: string, path: string, body?: unknown) => Promise<void>
  pending: string | null
}

function Spinner() {
  return <span className="spinner" aria-hidden="true" />
}

/** Quick actions row; Trancar/Destrancar and Buzina ask for an inline confirmation first. */
export function QuickActions({ car, run, pending }: Props) {
  const [confirming, setConfirming] = useState<string | null>(null)
  const busy = pending !== null
  const off = !car || busy

  function action(key: string, label: string, path: string, body?: unknown, opts?: { primary?: boolean; confirm?: string }): ReactNode {
    if (confirming === key) {
      return (
        <span key={key} className="inline-flex items-center gap-2 rounded-[9px] bg-warn-soft px-3 py-[6px] text-xs font-semibold text-warn-ink">
          {opts?.confirm}
          <button className="btn btn-primary !px-3 !py-1" onClick={() => { setConfirming(null); void run(key, path, body) }}>
            Sim
          </button>
          <button className="btn !px-3 !py-1" onClick={() => setConfirming(null)}>
            Não
          </button>
        </span>
      )
    }
    return (
      <button
        key={key}
        className={`btn ${opts?.primary ? 'btn-primary' : ''}`}
        disabled={off}
        onClick={() => (opts?.confirm ? setConfirming(key) : void run(key, path, body))}
      >
        {pending === key && <Spinner />}
        {label}
      </button>
    )
  }

  const locked = car?.locked ?? false
  return (
    <div className="flex flex-wrap gap-2">
      {action('charge', 'Carregar agora', '/api/control/charge-now', undefined, { primary: true })}
      {action('precond', car?.precond_on ? 'Parar climatização' : 'Climatizar', '/api/control/precondition', { on: !car?.precond_on })}
      {action('lock', locked ? 'Destrancar' : 'Trancar', '/api/control/lock', { locked: !locked }, { confirm: locked ? 'Destrancar o carro?' : 'Trancar o carro?' })}
      {action('lights', 'Luzes', '/api/control/lights')}
      {action('horn', 'Buzina', '/api/control/horn', undefined, { confirm: 'Tocar a buzina?' })}
    </div>
  )
}
