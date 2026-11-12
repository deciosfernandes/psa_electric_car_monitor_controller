import { useMutation, useQueryClient } from '@tanstack/react-query'
import { apiSend, type Place, type Status, type Tariff } from '../api'
import { fmtDate, fmtHour, fmtKm, fmtPct } from '../format'
import type { useControl } from './useControl'

const ACTION_TEXT = { defer: 'ativar diferido', charge: 'iniciar carga' } as const

function nextEventText(at: string, action: keyof typeof ACTION_TEXT): string {
  const sameDay = fmtDate(at) === fmtDate(new Date())
  return `${ACTION_TEXT[action]} ${sameDay ? 'às' : `a ${fmtDate(at).slice(0, 5)} às`} ${fmtHour(at)}`
}

export function ageText(min: number | null): string | null {
  if (min === null) return null
  if (min < 1) return 'agora mesmo'
  return min < 60 ? `há ${min} min` : `há ${Math.floor(min / 60)} h`
}

export function CableCard({ status }: { status: Status }) {
  const plugged = status.car?.plugged
  const age = ageText(status.position_age_min)
  const where = status.place_name ? `em ${status.place_name}` : status.where === 'unknown' ? 'posição desconhecida' : null
  return (
    <div className="card">
      <div className="label">Cabo</div>
      <div className="mt-1 text-xl font-bold">{plugged == null ? '—' : plugged ? '🔌 Ligado' : 'Desligado'}</div>
      <div className="mt-1 text-xs text-muted">{[where, age && `posição ${age}`].filter(Boolean).join(' · ')}</div>
    </div>
  )
}

export function MileageCard({ status }: { status: Status }) {
  const km = status.mileage_km ?? status.car?.mileage_km ?? null
  return (
    <div className="card">
      <div className="label">Quilometragem</div>
      <div className="big">{km != null ? fmtKm(Math.round(km)) : '—'}</div>
    </div>
  )
}

export function HealthCard({ status }: { status: Status }) {
  const health = status.car?.battery_health ?? null
  return (
    <div className="card">
      <div className="label">Vida útil da bateria</div>
      {health != null ? (
        <>
          <div className="big">{fmtPct(Math.round(health))}</div>
          <div className="mt-1 text-xs text-muted">valor reportado pela PSA</div>
        </>
      ) : (
        <div className="mt-2 text-sm text-muted">não disponível</div>
      )}
    </div>
  )
}

export function HomeBanner({ positionAgeMin = null }: { positionAgeMin?: number | null }) {
  const qc = useQueryClient()
  const define = useMutation({
    mutationFn: () => apiSend<Place>('POST', '/api/places/from-car', { name: 'Casa', kind: 'home' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['status'] }),
  })
  return (
    <div className="card mb-4 flex flex-wrap items-center gap-3 border border-line">
      <div className="flex-1 text-sm">
        <b>Defina o local Casa</b>
        <div className="text-xs text-muted">A automação só atua em Casa. Com o carro estacionado em casa, use a posição atual.</div>
        {ageText(positionAgeMin) && <div className="text-xs text-muted">Posição do carro {ageText(positionAgeMin)}</div>}
        {define.isError && (
          <div role="alert" className="mt-1 text-xs font-semibold text-danger-ink">
            {define.error instanceof Error ? define.error.message : 'Erro inesperado'}
          </div>
        )}
      </div>
      <button className="btn btn-primary" disabled={define.isPending} onClick={() => define.mutate()}>
        Usar posição atual do carro
      </button>
    </div>
  )
}

export function AutomationCard({
  status,
  tariff,
  run,
  pending,
}: {
  status: Status
  tariff?: Tariff
  run: ReturnType<typeof useControl>['run']
  pending: string | null
}) {
  const qc = useQueryClient()
  const { automation, override, next_event, last_command, where } = status
  const toggle = useMutation({
    mutationFn: () => apiSend('PUT', '/api/automation', { ...automation, enabled: !automation.enabled }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['status'] }),
  })
  const awayDeferred = (where === 'work' || where === 'other') && last_command?.mode === 'defer'
  return (
    <div className="card">
      <div className="flex items-center justify-between">
        <div className="label">Automação</div>
        <button
          role="switch"
          aria-checked={automation.enabled}
          aria-label="Automação"
          disabled={toggle.isPending}
          onClick={() => toggle.mutate()}
          className={`relative h-6 w-[42px] rounded-xl transition-colors ${automation.enabled ? 'bg-vazio' : 'bg-line'}`}
        >
          <span className={`absolute top-0.5 h-5 w-5 rounded-full bg-white transition-all ${automation.enabled ? 'right-0.5' : 'left-0.5'}`} />
        </button>
      </div>
      {override ? (
        <>
          <div className="mt-1 text-base font-bold">Carga manual ativa</div>
          <div className="mt-2 text-xs text-muted">Desde as {fmtHour(override.started_at)} · até ao limite ou desligar o cabo</div>
          <button className="btn mt-2 !py-1.5 !text-xs" disabled={pending !== null} onClick={() => void run('cancel', '/api/control/cancel-override')}>
            {pending === 'cancel' && <span className="spinner" aria-hidden="true" />}
            Cancelar
          </button>
        </>
      ) : (
        <>
          <div className="mt-1 text-base font-bold">
            {automation.enabled ? 'Ligada' : 'Desligada'} · só em Casa
            {tariff && <span className="text-xs font-normal text-muted"> · {tariff.name}</span>}
          </div>
          {automation.enabled && where !== 'home' && (
            <div className="mt-2 text-xs font-semibold">Em pausa · {status.paused_reason ?? 'fora de Casa'}</div>
          )}
          {automation.enabled && where === 'home' && (
            <div className="mt-2 text-xs">
              Próximo: {next_event ? <b>{nextEventText(next_event.at, next_event.action)}</b> : <span className="text-muted">sem eventos previstos</span>}
            </div>
          )}
          <div className="mt-1 text-xs text-muted">desvio {automation.offset_min} min · fora de Casa: sem comandos</div>
          {awayDeferred && <div className="mt-1 text-xs font-semibold">Programa diferido ativo no carro</div>}
        </>
      )}
      {toggle.isError && (
        <div role="alert" className="mt-2 text-xs font-semibold text-danger-ink">
          {toggle.error instanceof Error ? toggle.error.message : 'Erro inesperado'}
        </div>
      )}
    </div>
  )
}
