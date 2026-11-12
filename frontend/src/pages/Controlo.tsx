import { useState, type ReactNode } from 'react'
import { useStatus } from '../useStatus'
import { useControl } from '../components/useControl'

type Last = { key: string; ok: boolean; message: string } | null

function Result({ last, ids }: { last: Last; ids: string[] }) {
  if (!last || !ids.includes(last.key)) return null
  return (
    <div role="status" className={`mt-3 text-xs font-semibold ${last.ok ? 'text-ok-ink' : 'text-danger-ink'}`}>
      {last.ok ? `Confirmado · ${last.message}` : `Falhou: ${last.message}`}
    </div>
  )
}

function ControlCard({ title, state, children, last, ids }: { title: string; state?: string; children: ReactNode; last: Last; ids: string[] }) {
  return (
    <div className="card">
      <div className="flex items-center justify-between gap-2">
        <div className="label">{title}</div>
        {state && <span className="rounded-full bg-primary-soft px-2 py-0.5 text-[11px] font-semibold text-primary-ink">{state}</span>}
      </div>
      <div className="mt-3 flex flex-wrap gap-2">{children}</div>
      <Result last={last} ids={ids} />
    </div>
  )
}

export default function Controlo() {
  const { data } = useStatus()
  const { run, pending, last, toast } = useControl()
  const [confirmUnlock, setConfirmUnlock] = useState(false)
  const car = data?.car ?? null
  const off = !car || pending !== null

  const btn = (id: string, label: string, path: string, body?: unknown, primary?: boolean) => (
    <button className={`btn ${primary ? 'btn-primary' : ''}`} disabled={off} onClick={() => void run(id, path, body)}>
      {pending === id && <span className="spinner" aria-hidden="true" />}
      {label}
    </button>
  )

  const locked = car?.locked
  return (
    <section className="grid gap-[14px]">
      <h1 className="text-xl font-bold">Controlo</h1>
      {!car && <div className="card text-muted">Sem ligação ao carro: configure a conta PSA em Definições.</div>}
      <div className="grid gap-[14px] md:grid-cols-2">
        <ControlCard title="Carga" state={data?.override ? 'Carga manual ativa' : undefined} last={last} ids={['charge', 'cancel']}>
          {btn('charge', 'Carregar agora', '/api/control/charge-now', undefined, true)}
          {btn('cancel', 'Cancelar carga manual', '/api/control/cancel-override')}
        </ControlCard>

        <ControlCard title="Climatização" state={car ? (car.precond_on ? 'Ligada' : 'Desligada') : undefined} last={last} ids={['precond-on', 'precond-off']}>
          {btn('precond-on', 'Ligar', '/api/control/precondition', { on: true })}
          {btn('precond-off', 'Desligar', '/api/control/precondition', { on: false })}
        </ControlCard>

        <ControlCard title="Fechaduras" state={locked == null ? undefined : locked ? 'Trancado' : 'Destrancado'} last={last} ids={['lock', 'unlock']}>
          {btn('lock', 'Trancar', '/api/control/lock', { locked: true })}
          {confirmUnlock ? (
            <span className="inline-flex items-center gap-2 rounded-[9px] bg-warn-soft px-3 py-[6px] text-xs font-semibold text-warn-ink">
              Destrancar o carro?
              <button
                className="btn btn-primary !px-3 !py-1"
                disabled={off}
                onClick={() => {
                  setConfirmUnlock(false)
                  void run('unlock', '/api/control/lock', { locked: false })
                }}
              >
                Sim
              </button>
              <button className="btn !px-3 !py-1" onClick={() => setConfirmUnlock(false)}>
                Não
              </button>
            </span>
          ) : (
            <button className="btn" disabled={off} onClick={() => setConfirmUnlock(true)}>
              {pending === 'unlock' && <span className="spinner" aria-hidden="true" />}
              Destrancar
            </button>
          )}
        </ControlCard>

        <ControlCard title="Buzina e luzes" last={last} ids={['horn', 'lights']}>
          {btn('horn', 'Buzina', '/api/control/horn')}
          {btn('lights', 'Luzes', '/api/control/lights')}
        </ControlCard>

        <ControlCard title="Acordar o carro" last={last} ids={['wakeup']}>
          {btn('wakeup', 'Acordar carro', '/api/control/wakeup')}
        </ControlCard>
      </div>
      {toast}
    </section>
  )
}
