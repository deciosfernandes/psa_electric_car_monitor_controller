import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { apiGet, type Trip } from '../api'
import { MiniMap, type LatLon } from '../components/MiniMap'
import { PlaceBadge } from '../components/PlaceBadge'
import { fmtDate, fmtHour, fmtNum } from '../format'

const point = (lat: number | null, lon: number | null): LatLon[] => (lat != null && lon != null ? [{ lat, lon }] : [])

export default function Viagens() {
  const list = useQuery({ queryKey: ['trips'], queryFn: () => apiGet<Trip[]>('/api/trips?limit=500') })
  const [selected, setSelected] = useState<number | null>(null)
  if (list.isError) return <div className="card text-danger-ink">Não foi possível obter as viagens.</div>
  const rows = list.data ?? []
  const trip = rows.find((t) => t.id === selected)
  const markers = trip ? [...point(trip.from_lat, trip.from_lon), ...point(trip.to_lat, trip.to_lon)] : []

  return (
    <section>
      <h1 className="mb-1 text-xl font-bold">Viagens</h1>
      <div className="mb-4 text-xs text-muted">Horas e distâncias aproximadas (≈): deduzidas das leituras periódicas do carro.</div>
      <div className="grid gap-[14px] lg:grid-cols-[2fr_1fr]">
        <div className="card overflow-x-auto">
          <table className="w-full text-left text-[13px]">
            <thead className="text-xs text-muted">
              <tr>
                <th className="py-1 pr-3">Data</th>
                <th className="pr-3">Partida → Chegada</th>
                <th className="pr-3">Distância</th>
                <th className="pr-3">% gasto</th>
                <th className="pr-3">kWh</th>
                <th>kWh/100 km</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((t) => (
                <tr key={t.id} className={`cursor-pointer border-t border-line ${selected === t.id ? 'bg-line' : ''}`} onClick={() => setSelected(t.id)}>
                  <td className="py-2 pr-3">
                    {fmtDate(t.departed_at)} ≈{fmtHour(t.departed_at)}
                  </td>
                  <td className="pr-3">
                    <PlaceBadge kind={t.from_kind} name={t.from_name} lat={t.from_lat} lon={t.from_lon} /> → <PlaceBadge kind={t.to_kind} name={t.to_name} lat={t.to_lat} lon={t.to_lon} />
                  </td>
                  <td className="pr-3">≈ {fmtNum(t.km)} km</td>
                  <td className="pr-3">{t.soc_start != null && t.soc_end != null ? `${Math.round(t.soc_start - t.soc_end)} %` : '—'}</td>
                  <td className="pr-3">{fmtNum(t.kwh)}</td>
                  <td>{fmtNum(t.kwh_100km)}</td>
                </tr>
              ))}
              {rows.length === 0 && (
                <tr>
                  <td colSpan={6} className="py-3 text-muted">
                    Ainda sem viagens registadas.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        <div className="card">
          <div className="label">Mapa (partida/chegada)</div>
          <div className="mt-2">
            <MiniMap markers={markers} />
          </div>
          {!trip && <div className="mt-2 text-xs text-muted">Clique numa viagem para ver a partida e a chegada.</div>}
        </div>
      </div>
    </section>
  )
}
