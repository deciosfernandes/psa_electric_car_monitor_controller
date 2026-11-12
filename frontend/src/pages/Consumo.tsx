import { useQuery } from '@tanstack/react-query'
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { apiGet, type Consumption } from '../api'
import { fmtKm, fmtNum } from '../format'

const TICK = { fill: 'var(--muted)', fontSize: 11 }
const monthLabel = (m: string) => `${m.slice(5)}/${m.slice(2, 4)}` // MM/AA

export default function Consumo() {
  const q = useQuery({ queryKey: ['consumption'], queryFn: () => apiGet<Consumption>('/api/consumption') })
  if (q.isError) return <div className="card text-danger-ink">Não foi possível obter o consumo.</div>
  if (!q.data) return null
  const { last30, months } = q.data
  const data = months.flatMap((m) => (m.kwh_100km != null ? [{ month: monthLabel(m.month), v: m.kwh_100km }] : []))

  return (
    <section>
      <h1 className="mb-4 text-xl font-bold">Consumo</h1>
      <div className="grid gap-[14px] lg:grid-cols-[1fr_2fr]">
        <div className="card">
          <div className="label">Últimos 30 dias</div>
          <div className="big">
            {last30.kwh_100km != null ? fmtNum(last30.kwh_100km) : '—'} <span className="text-lg">kWh/100 km</span>
          </div>
          <div className="mt-1 text-xs text-muted">
            {fmtKm(last30.km)} · {fmtNum(last30.kwh)} kWh
          </div>
        </div>
        <div className="card">
          <div className="label">kWh/100 km por mês</div>
          {data.length === 0 ? (
            <div className="mt-3 text-[13px] text-muted">Ainda sem viagens registadas.</div>
          ) : (
            <div className="mt-2 h-[200px]" role="img" aria-label="Consumo mensal em kWh por 100 km">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={data} margin={{ top: 6, right: 8, bottom: 0, left: -18 }}>
                  <CartesianGrid stroke="var(--border)" vertical={false} />
                  <XAxis dataKey="month" tick={TICK} stroke="var(--border)" />
                  <YAxis tick={TICK} stroke="var(--border)" />
                  <Tooltip
                    formatter={(v) => [`${fmtNum(Number(v))} kWh/100 km`, 'Consumo']}
                    contentStyle={{ background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 8, color: 'var(--text)' }}
                  />
                  <Bar dataKey="v" fill="var(--primary)" radius={[4, 4, 0, 0]} isAnimationActive={false} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}
        </div>
      </div>
    </section>
  )
}
