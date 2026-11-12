import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { BatterySample } from '../api'
import { fmtHour, fmtPct } from '../format'

const TICK = { fill: 'var(--muted)', fontSize: 11 }

export function BatteryChart({ samples }: { samples: BatterySample[] }) {
  if (samples.length < 2) return <div className="mt-3 text-[13px] text-muted">Ainda sem dados de bateria.</div>
  const data = samples.map((s) => ({ t: new Date(s.at).getTime(), soc: s.soc }))
  return (
    <div className="mt-2 h-[170px]" role="img" aria-label="Gráfico da bateria nas últimas 24 horas">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 6, right: 8, bottom: 0, left: -18 }}>
          <defs>
            <linearGradient id="soc-fill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="var(--primary)" stopOpacity={0.3} />
              <stop offset="100%" stopColor="var(--primary)" stopOpacity={0.03} />
            </linearGradient>
          </defs>
          <CartesianGrid stroke="var(--border)" vertical={false} />
          <XAxis
            dataKey="t"
            type="number"
            scale="time"
            domain={['dataMin', 'dataMax']}
            tickFormatter={(t: number) => fmtHour(new Date(t))}
            tick={TICK}
            stroke="var(--border)"
            minTickGap={40}
          />
          <YAxis domain={[0, 100]} ticks={[0, 50, 100]} tickFormatter={(v: number) => `${v}%`} tick={TICK} stroke="var(--border)" />
          <Tooltip
            labelFormatter={(t) => fmtHour(new Date(Number(t)))}
            formatter={(v) => [fmtPct(Number(v)), 'Bateria']}
            contentStyle={{ background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 8, color: 'var(--text)' }}
          />
          <Area type="monotone" dataKey="soc" stroke="var(--primary)" strokeWidth={2.5} fill="url(#soc-fill)" isAnimationActive={false} />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  )
}
