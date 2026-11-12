import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { ChargeSample } from '../api'
import { fmtHour, fmtKm, fmtPct } from '../format'

const TICK = { fill: 'var(--muted)', fontSize: 11 }

/** Battery % (left axis) and charge rate in km/h (right axis) over one charge session. */
export function ChargeCurve({ samples }: { samples: ChargeSample[] }) {
  if (samples.length < 2) return <div className="mt-3 text-[13px] text-muted">Sem dados suficientes para a curva.</div>
  const data = samples.map((s) => ({ t: new Date(s.at).getTime(), soc: s.soc, rate: s.rate_kmh }))
  return (
    <div className="mt-3">
      <div className="h-[200px]" role="img" aria-label="Curva de carregamento">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 6, right: 0, bottom: 0, left: -18 }}>
            <CartesianGrid stroke="var(--border)" vertical={false} />
            <XAxis dataKey="t" type="number" scale="time" domain={['dataMin', 'dataMax']} tickFormatter={(t: number) => fmtHour(new Date(t))} tick={TICK} stroke="var(--border)" minTickGap={40} />
            <YAxis yAxisId="soc" domain={[0, 100]} ticks={[0, 50, 100]} tickFormatter={(v: number) => `${v}%`} tick={TICK} stroke="var(--border)" />
            <YAxis yAxisId="rate" orientation="right" tick={TICK} stroke="var(--border)" />
            <Tooltip
              labelFormatter={(t) => fmtHour(new Date(Number(t)))}
              formatter={(v, name) => (name === 'soc' ? [fmtPct(Number(v)), 'Bateria'] : [`${fmtKm(Number(v))}/h`, 'Ritmo'])}
              contentStyle={{ background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 8, color: 'var(--text)' }}
            />
            <Line yAxisId="soc" type="monotone" dataKey="soc" stroke="var(--primary)" strokeWidth={2.5} dot={false} isAnimationActive={false} />
            <Line yAxisId="rate" type="monotone" dataKey="rate" stroke="var(--muted)" strokeWidth={1.5} dot={false} connectNulls isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>
      <div className="text-[11px] text-muted">Linha colorida: bateria (%) · linha cinzenta: ritmo de carga (km/h)</div>
    </div>
  )
}
