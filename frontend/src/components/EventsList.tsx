import type { ScheduleEvent } from '../api'
import { fmtDate, fmtHour } from '../format'

const weekdayFmt = new Intl.DateTimeFormat('pt-PT', { timeZone: 'Atlantic/Azores', weekday: 'short' })
const TEXT = { charge: 'Desligar diferido (carregar)', defer: 'Ligar diferido' } as const

const dayLabel = (at: string) => `${weekdayFmt.format(new Date(at)).slice(0, 3)} ${fmtDate(at).slice(0, 5)}`

/** Planned automation events grouped by Azores calendar day, e.g. "qui 12/11 · 00h10 Desligar diferido (carregar)". */
export function EventsList({ events }: { events: ScheduleEvent[] }) {
  if (!events.length) return <div className="mt-3 text-[13px] text-muted">Sem eventos previstos.</div>
  const days: { key: string; label: string; items: ScheduleEvent[] }[] = []
  for (const e of events) {
    const key = fmtDate(e.at)
    const last = days[days.length - 1]
    if (last?.key === key) last.items.push(e)
    else days.push({ key, label: dayLabel(e.at), items: [e] })
  }
  return (
    <div className="mt-2 grid gap-x-6 gap-y-2 md:grid-cols-2">
      {days.map((d) => (
        <section key={d.key} aria-label={d.label} className="flex gap-3 border-b border-line py-1.5 text-[13px]">
          <b className="w-[72px] shrink-0">{d.label}</b>
          <ul className="m-0 list-none p-0">
            {d.items.map((e) => (
              <li key={`${e.at}-${e.action}`}>
                <b>{fmtHour(e.at)}</b> {TEXT[e.action]}
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  )
}
