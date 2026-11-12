import { useQuery } from '@tanstack/react-query'
import { apiGet, type Activity, type BatterySample, type Tariff } from '../api'
import { ActivityList } from '../components/ActivityList'
import { BatteryChart } from '../components/BatteryChart'
import { AutomationCard, CableCard, HealthCard, HomeBanner, MileageCard } from '../components/PainelCards'
import { QuickActions } from '../components/QuickActions'
import { StatusPill } from '../components/StatusPill'
import { TariffStrip } from '../components/TariffStrip'
import { useControl } from '../components/useControl'
import { fmtHour, fmtKm } from '../format'
import { pillState } from '../lib/pill'
import { useStatus } from '../useStatus'

const REFRESH = 30_000
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

      {status.where === 'unset' && <HomeBanner positionAgeMin={status.position_age_min} />}

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
        <CableCard status={status} />
        <MileageCard status={status} />
        <HealthCard status={status} />
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
