// Types mirror backend/psacc/api/*.py (datetimes are ISO-8601 strings with offset).

export type PeriodType = 'vazio' | 'cheias' | 'ponta'
export type Action = 'charge' | 'defer'
export type DayType = 'weekday' | 'saturday' | 'sunday'

export interface CarSnapshot {
  vin: string
  at: string
  soc: number | null
  range_km: number | null
  plugged: boolean | null
  charging: boolean | null
  charge_status: string | null
  charge_rate_kmh: number | null
  next_delayed_time: string | null
  latitude: number | null
  longitude: number | null
  locked: boolean | null
  precond_on: boolean | null
  mileage_km: number | null
  battery_health: number | null // % reported by PSA
}

export interface ScheduleEvent {
  at: string
  action: Action
}

export interface Override {
  started_at: string
  reason: string
}

export interface LastCommand {
  mode: Action
  at: string
  verified: boolean
  attempts: number
  reason: string
  alerted: boolean
  program_hour: [number, number] | null
}

export type Where = 'home' | 'work' | 'other' | 'unknown' | 'unset'

export interface Place {
  id: number | null
  name: string
  kind: 'home' | 'work' | 'other'
  lat: number
  lon: number
  radius_m: number
  price_eur_kwh: number | null
}

export interface Status {
  car: CarSnapshot | null // null while the PSA account is not configured or awaiting first poll
  where: Where
  place_name: string | null
  mileage_km: number | null
  position_age_min: number | null
  period: PeriodType
  next_event: ScheduleEvent | null
  automation: { enabled: boolean; offset_min: number; battery_limit: number }
  override: Override | null
  last_command: LastCommand | null
  mqtt_connected: boolean
  psa_connected: boolean
  updated_at: string
}

export interface Slot {
  start: number // minutes of the local day, 0..1440
  end: number
  period: PeriodType
}

export interface Season {
  name: string
  months: number[]
  days: Record<DayType, Slot[]>
}

export interface Price {
  period: PeriodType
  eur_kwh: number
  valid_from: string
}

export interface Tariff {
  name: string
  seasons: Season[]
  prices: Price[]
}

export interface Activity {
  at: string
  kind: 'info' | 'action' | 'error'
  message: string
  source: 'automation' | 'control' // 'control': a command sent from the Controlo page
}

export interface BatterySample {
  at: string
  soc: number
  charging: boolean
}

export interface AuthState {
  configured: boolean
  logged_in: boolean
}

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = {}
  if (method !== 'GET') headers['X-PSACC'] = '1' // CSRF guard: required on every non-GET request
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  const res = await fetch(path, {
    method,
    headers,
    credentials: 'same-origin',
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (res.status === 401 && !path.startsWith('/api/auth/') && window.location.pathname !== '/login') {
    window.location.assign('/login')
  }
  if (!res.ok) {
    let detail = `Erro ${res.status}`
    try {
      const data = await res.json()
      if (typeof data?.detail === 'string') detail = data.detail
    } catch {
      /* body is not JSON */
    }
    throw new ApiError(res.status, detail)
  }
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T)
}

export const apiGet = <T,>(path: string) => request<T>('GET', path)
export const apiSend = <T,>(method: 'POST' | 'PUT' | 'DELETE' | 'PATCH', path: string, body?: unknown) =>
  request<T>(method, path, body)

export interface ChargeSession {
  id: number
  started_at: string
  ended_at: string | null
  place_kind: 'home' | 'work' | 'other'
  place_name: string | null
  soc_start: number
  soc_end: number | null
  kwh: number | null
  cost_eur: number | null // null = unknown price
  price_override_eur_kwh: number | null
  partial: boolean // session seen only in part: kWh and cost are approximate
}

export interface ChargeSample {
  at: string
  soc: number
  rate_kmh: number | null
}

export interface ChargeSummary {
  kwh: number
  cost_eur: number // unknown-cost sessions count as 0
  work_pct: number
  home_pct: number
  avg_home_eur_kwh: number | null
}

export interface Trip {
  id: number
  departed_at: string
  arrived_at: string
  from_kind: 'home' | 'work' | 'other'
  from_name: string | null
  to_kind: 'home' | 'work' | 'other'
  to_name: string | null
  from_lat: number | null
  from_lon: number | null
  to_lat: number | null
  to_lon: number | null
  km: number
  soc_start: number | null
  soc_end: number | null
  kwh: number
  kwh_100km: number
}

export interface ConsumptionMonth {
  month: string // YYYY-MM
  km: number
  kwh: number
  kwh_100km: number | null
}

export interface Consumption {
  last30: { km: number; kwh: number; kwh_100km: number | null }
  months: ConsumptionMonth[]
}
