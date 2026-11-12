import type { Activity } from '../api'
import { fmtDate, fmtHour } from '../format'

const when = (at: string) =>
  fmtDate(at) === fmtDate(new Date()) ? fmtHour(at) : `${fmtDate(at).slice(0, 5)} ${fmtHour(at)}`

export function ActivityList({ items }: { items: Activity[] }) {
  if (!items.length) return <div className="mt-3 text-[13px] text-muted">Sem atividade registada.</div>
  return (
    <ul className="mt-2 list-none p-0">
      {items.map((a) => (
        <li key={`${a.at}-${a.message}`} className="flex gap-[10px] border-b border-line py-[6px] text-xs last:border-0">
          <b className="shrink-0">
            {when(a.at)}
          </b>
          <span className={a.kind === 'error' ? 'text-danger-ink' : ''}>{a.message}</span>
        </li>
      ))}
    </ul>
  )
}
