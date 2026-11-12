import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { apiGet, apiSend, type Place } from '../api'
import { MiniMap } from './MiniMap'

const KIND_TEXT = { home: 'Casa', work: 'Trabalho', other: 'Outro' } as const
const errorText = (e: unknown) => (e instanceof Error ? e.message : 'Erro inesperado')

// Map starts over Ponta Delgada until a point is chosen.
const blank = (): Place => ({ id: null, name: '', kind: 'other', lat: 37.74, lon: -25.67, radius_m: 150, price_eur_kwh: null })

export function PlacesEditor() {
  const qc = useQueryClient()
  const places = useQuery({ queryKey: ['places'], queryFn: () => apiGet<Place[]>('/api/places') })
  const [draft, setDraft] = useState<Place | null>(null)
  const done = () => {
    resetErrors()
    setDraft(null)
    void qc.invalidateQueries({ queryKey: ['places'] })
    void qc.invalidateQueries({ queryKey: ['status'] })
  }
  const save = useMutation({
    mutationFn: (p: Place) => (p.id === null ? apiSend('POST', '/api/places', p) : apiSend('PUT', `/api/places/${p.id}`, p)),
    onSuccess: done,
  })
  const remove = useMutation({ mutationFn: (id: number) => apiSend('DELETE', `/api/places/${id}`), onSuccess: done })
  const fromCar = useMutation({
    mutationFn: (p: Place) => apiSend<Place>('POST', '/api/places/from-car', { name: p.name, kind: p.kind }),
    onSuccess: done,
  })
  const resetErrors = () => [save, remove, fromCar].forEach((m) => m.reset())
  const open = (p: Place | null) => {
    resetErrors()
    setDraft(p)
  }
  const error = [save, remove, fromCar].find((m) => m.isError)?.error
  const set = (patch: Partial<Place>) => setDraft((d) => (d ? { ...d, ...patch } : d))
  const busy = save.isPending || fromCar.isPending

  return (
    <div className="card">
      <div className="flex items-center justify-between">
        <div className="label">Locais</div>
        {!draft && (
          <button className="btn" onClick={() => open(blank())}>
            Adicionar local
          </button>
        )}
      </div>
      <ul className="mt-3 grid gap-2">
        {places.data?.map((p) => (
          <li key={p.id} className="flex flex-wrap items-center gap-2 text-[13px]">
            <b>{p.name}</b>
            <span className="rounded-full bg-line px-2 py-0.5 text-[11px] font-semibold">{KIND_TEXT[p.kind]}</span>
            <span className="text-muted">
              {p.radius_m} m{p.price_eur_kwh != null && ` · ${p.price_eur_kwh} €/kWh`}
            </span>
            <button className="btn !py-1 !text-xs" onClick={() => open(p)}>
              Editar
            </button>
            <button className="btn !py-1 !text-xs" disabled={remove.isPending} onClick={() => p.id !== null && remove.mutate(p.id)}>
              Remover
            </button>
          </li>
        ))}
        {places.data?.length === 0 && !draft && <li className="text-xs text-muted">Sem locais definidos.</li>}
      </ul>
      {draft && (
        <form
          className="mt-4 grid gap-3 border-t border-line pt-4"
          onSubmit={(e) => {
            e.preventDefault()
            save.mutate(draft)
          }}
        >
          <div className="grid gap-3 md:grid-cols-2">
            <label className="block text-xs font-semibold">
              Nome
              <input className="field mt-1" maxLength={60} value={draft.name} onChange={(e) => set({ name: e.target.value })} />
            </label>
            <label className="block text-xs font-semibold">
              Tipo
              <select
                className="field mt-1"
                value={draft.kind}
                onChange={(e) => {
                  const kind = e.target.value as Place['kind']
                  set({ kind, price_eur_kwh: kind === 'other' ? draft.price_eur_kwh : null })
                }}
              >
                {Object.entries(KIND_TEXT).map(([k, t]) => (
                  <option key={k} value={k}>
                    {t}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <label className="block text-xs font-semibold">
            Raio: {draft.radius_m} m
            <input type="range" min={25} max={2000} step={25} className="mt-1 block w-full" value={draft.radius_m} onChange={(e) => set({ radius_m: Number(e.target.value) })} />
          </label>
          {draft.kind === 'other' && (
            <label className="block text-xs font-semibold">
              Preço (€/kWh)
              <input
                type="number"
                min={0}
                step={0.01}
                className="field mt-1 !w-40"
                value={draft.price_eur_kwh ?? ''}
                onChange={(e) => set({ price_eur_kwh: e.target.value === '' ? null : Number(e.target.value) })}
              />
            </label>
          )}
          <MiniMap markers={[{ lat: draft.lat, lon: draft.lon }]} radiusM={draft.radius_m} onPick={(lat, lon) => set({ lat, lon })} />
          <div className="text-xs text-muted">
            Clique no mapa para definir o ponto ({draft.lat.toFixed(5)}, {draft.lon.toFixed(5)}).
          </div>
          <div className="flex flex-wrap gap-2">
            <button className="btn btn-primary" type="submit" disabled={draft.name.trim() === '' || busy}>
              Guardar
            </button>
            {draft.id === null && (
              <button type="button" className="btn" disabled={draft.name.trim() === '' || busy} onClick={() => fromCar.mutate(draft)}>
                Usar posição atual do carro
              </button>
            )}
            <button type="button" className="btn" onClick={() => open(null)}>
              Cancelar
            </button>
          </div>
        </form>
      )}
      {error != null && (
        <div role="alert" className="mt-2 rounded-[9px] bg-danger-soft px-3 py-2 text-xs font-semibold text-danger-ink">
          {errorText(error)}
        </div>
      )}
    </div>
  )
}
