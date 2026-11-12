import { useEffect, useState } from 'react'
import type { PeriodType, Tariff } from '../api'
import { azoresNow, daySegments, seasonAt } from '../lib/tariff'

const COLOR: Record<PeriodType, string> = { vazio: 'var(--vazio)', cheias: 'var(--cheias)', ponta: 'var(--ponta)' }
const WEEKDAY = new Intl.DateTimeFormat('pt-PT', { timeZone: 'Atlantic/Azores', weekday: 'long' })

const clock = (m: number) => `${String(Math.floor(m / 60)).padStart(2, '0')}h${m % 60 ? String(m % 60).padStart(2, '0') : ''}`
const pct = (m: number) => `${(m / 1440) * 100}%`

export function TariffStrip({ tariff }: { tariff: Tariff }) {
  const [now, setNow] = useState(() => new Date())
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 30_000)
    return () => clearInterval(id)
  }, [])

  const segments = daySegments(tariff, now)
  const season = seasonAt(tariff, now)
  const { minute } = azoresNow(now)
  const ticks = [...new Set([0, ...segments.map((s) => s.end)])]
  const summary = segments.map((s) => `${s.period} ${clock(s.start)}–${clock(s.end)}`).join(', ')

  return (
    <div className="card">
      <div className="flex justify-between">
        <div className="label">
          Hoje · {WEEKDAY.format(now).split('-')[0]}
          {season && ` · ${season.name.toLowerCase()}`}
        </div>
        <div className="text-[11px] text-muted">
          {(['vazio', 'cheias', 'ponta'] as const).map((p) => (
            <span key={p} className="ml-3">
              <span style={{ color: COLOR[p] }} aria-hidden="true">
                ■
              </span>{' '}
              {p}
            </span>
          ))}
        </div>
      </div>
      <div className="relative mt-2 h-5 overflow-hidden rounded-[7px] bg-line">
        <div className="flex h-full" role="img" aria-label={`Períodos de hoje: ${summary}`}>
          {segments.map((s) => (
            <span
              key={s.start}
              title={`${s.period} · ${clock(s.start)}–${clock(s.end)}`}
              style={{ width: pct(s.end - s.start), background: COLOR[s.period] }}
            />
          ))}
        </div>
        <span
          data-testid="now-marker"
          title={`Agora · ${clock(minute)}`}
          className="absolute inset-y-0 w-[3px] -translate-x-1/2 rounded bg-ink ring-1 ring-card"
          style={{ left: pct(minute) }}
        />
      </div>
      <div className="relative mt-1 h-3 text-[10px] text-muted">
        {ticks.map((m) => (
          <span
            key={m}
            className={`absolute ${m === 0 ? '' : m === 1440 ? '-translate-x-full' : '-translate-x-1/2'}`}
            style={{ left: pct(m) }}
          >
            {clock(m)}
          </span>
        ))}
      </div>
    </div>
  )
}
