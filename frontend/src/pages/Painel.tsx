import { useQuery } from '@tanstack/react-query'
import { apiGet, type Activity, type BatterySample, type Status, type Tariff } from '../api'
import { ActivityList } from '../components/ActivityList'
import { BatteryChart } from '../components/BatteryChart'
import { QuickActions } from '../components/QuickActions'
import { StatusPill } from '../components/StatusPill'
import { TariffStrip } from '../components/TariffStrip'
import { useControl } from '../components/useControl'
import { fmtDate, fmtHour, fmtKm } from '../format'
import { pillState } from '../lib/pill'
import { useStatus } from '../useStatus'

const REFRESH = 30_000
const ACTION_TEXT = { defer: 'ativar diferido', charge: 'iniciar carga' } as const

function nextEventText(at: string, action: keyof typeof ACTION_TEXT): string {
  const sameDay = fmtDate(at) === fmtDate(new Date())
  return `${ACTION_TEXT[action]} ${sameDay ? 'às' : `a ${fmtDate(at).slice(0, 5)} às`} ${fmtHour(at)}`
}

function AutomationCard({ status, tariff, run, pending }: { status: Status; tariff?: Tariff; run: ReturnType<typeof useControl>['run']; pending: string | null }) {
  const { automation, override, next_event } = status
  return (
    <div className="card">
      <div className="label">Automação</div>
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
            {automation.enabled ? 'Ativa' : 'Desligada'}
            {tariff && ` · ${tariff.name}`}
          </div>
          {automation.enabled && (
            <div className="mt-2 text-xs">
              Próximo: {next_event ? <b>{nextEventText(next_event.at, next_event.action)}</b> : <span className="text-muted">sem eventos previstos</span>}
            </div>
          )}
          <div className="mt-1 text-xs text-muted">desvio {automation.offset_min} min</div>
        </>
      )}
    </div>
  )
}

export default function Painel() {
  const { data: status, isError } = useStatus()
  const tariff = useQuery({ queryKey: ['tariff'], queryFn: () => apiGet<Tariff>('/api/tariff') })
  const battery = useQuery({
    queryKey: ['battery', 24],
    queryFn: () => apiGet<BatterySample[]>('/api/battery?hours=24'),
    refetchInterval: REFRESH,
  })
  const activity = useQuery({
    queryKey: ['activity', 8],
    queryFn: () => apiGet<Activity[]>('/api/activity?limit=8'),
    refetchInterval: REFRESH,
  })
  const { run, pending, toast } = useControl()

  if (isError) return <div className="card text-danger-ink">Não foi possível obter o estado do carro.</div>
  if (!status) return null

  const car = status.car
  const pill = pillState(status, activity.data)
  const charging = car?.charging === true

  return (
    <section>
      <div className="mb-4 flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold">Peugeot e-208</h1>
          <div className="text-xs text-muted">
            {car ? `${car.locked === null ? '' : car.locked ? 'Trancado · ' : 'Destrancado · '}atualizado às ${fmtHour(car.at)}` : 'Sem ligação ao carro'}
          </div>
        </div>
        {pill && <StatusPill state={pill} />}
      </div>

      <div className="grid gap-[14px] md:grid-cols-3">
        <div className="card">
          <div className="label">Bateria</div>
          <div className="big">
            {car?.soc != null ? Math.round(car.soc) : '—'}
            <span className="text-xl">%</span>
          </div>
          <div className="mt-2 h-[14px] overflow-hidden rounded-[7px] bg-line">
            <div className="h-full bg-vazio" style={{ width: `${car?.soc ?? 0}%` }} />
          </div>
          <div className="mt-1.5 text-xs text-muted">Limite {status.automation.battery_limit} %</div>
        </div>
        <div className="card">
          <div className="label">Autonomia</div>
          <div className="big">
            {car?.range_km != null ? Math.round(car.range_km) : '—'}
            <span className="text-lg"> km</span>
          </div>
          <div className="mt-3 text-xs text-muted">
            {charging && car.charge_rate_kmh ? `A carregar · +${fmtKm(car.charge_rate_kmh)}/h` : 'Sem carga em curso'}
          </div>
        </div>
        <AutomationCard status={status} tariff={tariff.data} run={run} pending={pending} />
      </div>

      <div className="mt-[14px]">{tariff.data && <TariffStrip tariff={tariff.data} />}</div>

      <div className="mt-[14px] grid gap-[14px] lg:grid-cols-[2fr_1fr]">
        <div className="card">
          <div className="label">Bateria · últimas 24h</div>
          <BatteryChart samples={battery.data ?? []} />
        </div>
        <div className="card">
          <div className="label">Atividade</div>
          <ActivityList items={activity.data ?? []} />
        </div>
      </div>

      <div className="mt-[14px]">
        <QuickActions car={car} run={run} pending={pending} />
      </div>
      {toast}
    </section>
  )
}
