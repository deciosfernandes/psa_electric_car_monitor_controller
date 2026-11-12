const TZ = 'Atlantic/Azores'
const LOCALE = 'pt-PT'

const hourFmt = new Intl.DateTimeFormat(LOCALE, { timeZone: TZ, hour: '2-digit', minute: '2-digit', hourCycle: 'h23' })
const dateFmt = new Intl.DateTimeFormat(LOCALE, { timeZone: TZ, day: '2-digit', month: '2-digit', year: 'numeric' })
const eurFmt = new Intl.NumberFormat(LOCALE, { style: 'currency', currency: 'EUR' })
const intFmt = new Intl.NumberFormat(LOCALE, { maximumFractionDigits: 0 })

// Intl uses a no-break space before "%"/"km"/"€"; plain spaces keep the output predictable (wrapping is handled in CSS).
const plain = (s: string) => s.replace(/ /g, ' ')

export function fmtHour(d: Date | string): string {
  const parts = hourFmt.formatToParts(new Date(d))
  const get = (t: string) => parts.find((p) => p.type === t)?.value ?? '00'
  return `${get('hour')}h${get('minute')}`
}

export const fmtDate = (d: Date | string): string => dateFmt.format(new Date(d))
export const fmtPct = (n: number): string => `${intFmt.format(n)} %`
export const fmtKm = (n: number): string => `${intFmt.format(n)} km`
export const fmtEur = (n: number): string => plain(eurFmt.format(n))

/** Minutes since midnight as prose (06h50, 18h). */
export const fmtMinutes = (m: number): string =>
  `${String(Math.floor(m / 60)).padStart(2, '0')}h${m % 60 ? String(m % 60).padStart(2, '0') : ''}`

/** Minutes since midnight as an input value (06:50, 24:00). */
export const fmtClock = (m: number): string => `${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`
