import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { apiGet, apiSend, type ChargeSample, type ChargeSession, type ChargeSummary } from '../api'
import { ChargeCurve } from '../components/ChargeCurve'
import { PlaceBadge } from '../components/PlaceBadge'
import { fmtDate, fmtEur, fmtHour, fmtNum, fmtPct } from '../format'

const MONTH_NAME = new Intl.DateTimeFormat('pt-PT', { timeZone: 'Atlantic/Azores', month: 'long' })

function PriceEditor({ id, onDone }: { id: number; onDone: () => void }) {
  const qc = useQueryClient()
  const [value, setValue] = useState('')
  const save = useMutation({
    mutationFn: (eur: number) => apiSend('PUT', `/api/charges/${id}/price`, { eur_kwh: eur }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['charges'] })
      void qc.invalidateQueries({ queryKey: ['charges-summary'] })
      onDone()
    },
  })
  return (
    <form
      className="flex items-center gap-1"
      onClick={(e) => e.stopPropagation()}
      onSubmit={(e) => {
        e.preventDefault()
        const n = Number(value.replace(',', '.'))
        if (value.trim() !== '' && Number.isFinite(n) && n >= 0) save.mutate(n)
      }}
    >
      <input aria-label="Preço (€/kWh)" className="field !w-20 !py-1" inputMode="decimal" placeholder="€/kWh" value={value} onChange={(e) => setValue(e.target.value)} />
      <button className="btn btn-primary !px-2 !py-1 !text-xs" type="submit" disabled={save.isPending}>
        Guardar
      </button>
      <button className="btn !px-2 !py-1 !text-xs" type="button" onClick={onDone}>
        Cancelar
      </button>
      {save.isError && (
        <span role="alert" className="text-xs text-danger-ink">
          Erro ao guardar
        </span>
      )}
    </form>
  )
}

export default function Cargas() {
  const list = useQuery({ queryKey: ['charges'], queryFn: () => apiGet<ChargeSession[]>('/api/charges?limit=500') })
  const summary = useQuery({ queryKey: ['charges-summary'], queryFn: () => apiGet<ChargeSummary>('/api/charges/summary') })
  const [today] = useState(() => new Date())
  const [selected, setSelected] = useState<number | null>(null)
  const [editing, setEditing] = useState<number | null>(null)
  const samples = useQuery({
    queryKey: ['charge-samples', selected],
    queryFn: () => apiGet<ChargeSample[]>(`/api/charges/${selected}/samples`),
    enabled: selected !== null,
  })

  if (list.isError || summary.isError) return <div className="card text-danger-ink">Não foi possível obter as cargas.</div>
  const rows = list.data ?? []
  const s = summary.data
  // The summary counts unknown-cost sessions as 0 EUR: the totals are exact only when every session of the month is complete and priced.
  const inexact = s != null && (s.unpriced > 0 || s.partial > 0)
  const approx = inexact ? '≈ ' : ''

  return (
    <section>
      <h1 className="mb-4 text-xl font-bold">Cargas</h1>
      {s && (
        <div className="mb-[14px] grid gap-[14px] md:grid-cols-4">
          <div className="card">
            <div className="label">{MONTH_NAME.format(today)} · carregado</div>
            <div className="big">
              {approx}
              {fmtNum(s.kwh)} <span className="text-lg">kWh</span>
            </div>
          </div>
          <div className="card">
            <div className="label">Custo</div>
            <div className="big">
              {approx}
              {fmtEur(s.cost_eur)}
            </div>
            {inexact && <div className="mt-1 text-xs text-muted">Há cargas sem preço ou incompletas</div>}
          </div>
          <div className="card">
            <div className="label">Trabalho / Casa</div>
            <div className="big !text-xl">
              {fmtPct(s.work_pct)} / {fmtPct(s.home_pct)}
            </div>
            <div className="mt-2 h-[8px] overflow-hidden rounded bg-line">
              <div className="h-full bg-vazio" style={{ width: `${s.work_pct}%` }} />
            </div>
          </div>
          <div className="card">
            <div className="label">€/kWh médio em casa</div>
            <div className="big">{s.avg_home_eur_kwh != null ? fmtEur(s.avg_home_eur_kwh) : '—'}</div>
          </div>
        </div>
      )}
      <div className="card overflow-x-auto">
        <table className="w-full text-left text-[13px]">
          <thead className="text-xs text-muted">
            <tr>
              <th className="py-1 pr-3">Data</th>
              <th className="pr-3">Local</th>
              <th className="pr-3">Início → Fim</th>
              <th className="pr-3">Bateria</th>
              <th className="pr-3">kWh</th>
              <th>Custo</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((c) => {
              const a = c.partial ? '≈ ' : ''
              return (
                <tr key={c.id} className={`cursor-pointer border-t border-line ${selected === c.id ? 'bg-line' : ''}`} onClick={() => setSelected(c.id)}>
                  <td className="py-2 pr-3">{fmtDate(c.started_at)}</td>
                  <td className="pr-3">
                    <PlaceBadge kind={c.place_kind} name={c.place_name} />
                  </td>
                  <td className="pr-3">
                    {fmtHour(c.started_at)} → {c.ended_at ? fmtHour(c.ended_at) : 'em curso'}
                  </td>
                  <td className="pr-3">
                    {Math.round(c.soc_start)} % → {c.soc_end != null ? `${Math.round(c.soc_end)} %` : '—'}
                  </td>
                  <td className="pr-3">{c.kwh != null ? `${a}${fmtNum(c.kwh)}` : '—'}</td>
                  <td>
                    {editing === c.id ? (
                      <PriceEditor id={c.id} onDone={() => setEditing(null)} />
                    ) : c.cost_eur === null ? (
                      <button
                        className="underline"
                        onClick={(e) => {
                          e.stopPropagation()
                          setEditing(c.id)
                        }}
                      >
                        definir preço
                      </button>
                    ) : (
                      <>
                        {a}
                        {fmtEur(c.cost_eur)}
                        {c.place_kind === 'other' && (
                          <button
                            className="ml-2 text-xs text-muted underline"
                            onClick={(e) => {
                              e.stopPropagation()
                              setEditing(c.id)
                            }}
                          >
                            alterar preço
                          </button>
                        )}
                      </>
                    )}
                  </td>
                </tr>
              )
            })}
            {rows.length === 0 && (
              <tr>
                <td colSpan={6} className="py-3 text-muted">
                  Ainda sem cargas registadas.
                </td>
              </tr>
            )}
          </tbody>
        </table>
        {selected !== null ? (
          <ChargeCurve samples={samples.data ?? []} />
        ) : (
          rows.length > 0 && <div className="mt-3 text-xs text-muted">Clique numa carga para ver a curva de carregamento.</div>
        )}
      </div>
    </section>
  )
}
