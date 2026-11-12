import type { DayType, PeriodType, Slot, Tariff } from '../api'

const TZ = 'Atlantic/Azores'
const partsFmt = new Intl.DateTimeFormat('en-US', {
  timeZone: TZ,
  year: 'numeric',
  month: 'numeric',
  day: 'numeric',
  hour: 'numeric',
  minute: 'numeric',
  hourCycle: 'h23',
})

export interface AzoresClock {
  year: number
  month: number // 1..12
  day: number
  minute: number // minutes since local midnight
  dayType: DayType
}

/** Calendar date and time of day of an instant in Atlantic/Azores (never the browser's zone). */
export function azoresNow(at: Date = new Date()): AzoresClock {
  const get = (t: string) => Number(partsFmt.formatToParts(at).find((p) => p.type === t)?.value)
  const [year, month, day] = [get('year'), get('month'), get('day')]
  const wd = new Date(Date.UTC(year, month - 1, day)).getUTCDay() // 0 = Sunday
  const dayType: DayType = wd === 6 ? 'saturday' : wd === 0 ? 'sunday' : 'weekday'
  return { year, month, day, minute: get('hour') * 60 + get('minute'), dayType }
}

export function seasonAt(t: Tariff, at: Date) {
  const { month } = azoresNow(at)
  return t.seasons.find((s) => s.months.includes(month))
}

/** Period slots (minutes of the Azores day) that apply on the Azores calendar day of `day`. */
export function daySegments(t: Tariff, day: Date): { start: number; end: number; period: PeriodType }[] {
  const season = seasonAt(t, day)
  if (!season) return []
  const slots: Slot[] = season.days[azoresNow(day).dayType]
  return slots.map(({ start, end, period }) => ({ start, end, period }))
}
