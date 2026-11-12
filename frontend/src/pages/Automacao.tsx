import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { apiGet, apiSend, type Activity, type ScheduleEvent, type Tariff } from '../api'
import { ActivityList } from '../components/ActivityList'
import { EventsList } from '../components/EventsList'
import { PriceTable, TariffEditor } from '../components/TariffEditor'
import { azoresNow } from '../lib/tariff'

interface Automation {
  enabled: boolean
  battery_limit_enabled: boolean
  offset_min: number
  battery_limit: number
}

const errorText = (e: unknown) => (e instanceof Error ? e.message : 'Erro inesperado')
const Dirty = () => <span className="rounded-full bg-warn-soft px-2 py-0.5 text-[11px] font-semibold text-warn-ink">Alterações por guardar</span>
const Saved = () => <span className="text-[11px] font-semibold text-ok-ink">Guardado</span>

function ErrorBox({ message }: { message: string | null }) {
  return message ? (
    <div role="alert" className="mt-2 rounded-[9px] bg-danger-soft px-3 py-2 text-xs font-semibold text-danger-ink">
      {message}
    </div>
  ) : null
}

function SettingsCard({ saved }: { saved: Automation }) {
  const qc = useQueryClient()
  const [draft, setDraft] = useState<Automation | null>(null)
  const [justSaved, setJustSaved] = useState(false)
  const form = draft ?? saved
  const dirty = draft !== null && JSON.stringify(draft) !== JSON.stringify(saved)
  const offsetOk = Number.isInteger(form.offset_min) && form.offset_min >= 0 && form.offset_min <= 60

  const save = useMutation({
    mutationFn: () => apiSend<Automation>('PUT', '/api/automation', form),
    onSuccess: (data) => {
      qc.setQueryData(['automation'], data)
      setDraft(null)
      setJustSaved(true)
      void qc.invalidateQueries({ queryKey: ['status'] })
      void qc.invalidateQueries({ queryKey: ['events'] })
    },
  })
  const edit = (patch: Partial<Automation>) => {
    setJustSaved(false)
    save.reset()
    setDraft({ ...form, ...patch })
  }

  return (
    <div className="card">
      <div className="flex items-center justify-between">
        <div className="label">Carregamento diferido automático</div>
        {dirty ? <Dirty /> : justSaved && <Saved />}
      </div>
      <div className="mt-3 grid gap-5 md:grid-cols-3">
        <div>
          <div className="text-xs font-semibold" id="auto-switch">
            Automação (horário em Casa)
          </div>
          <button
            role="switch"
            aria-checked={form.enabled}
            aria-labelledby="auto-switch"
            onClick={() => edit({ enabled: !form.enabled })}
            className={`mt-2 inline-flex h-7 w-12 items-center rounded-full p-0.5 transition-colors ${form.enabled ? 'bg-primary' : 'bg-line'}`}
          >
            <span className={`h-6 w-6 rounded-full bg-white shadow transition-transform ${form.enabled ? 'translate-x-5' : ''}`} />
          </button>
          <span className="ml-2 align-middle text-xs text-muted">{form.enabled ? 'Ativa' : 'Desligada'}</span>
        </div>
        <label className="block text-xs font-semibold">
          Desvio (minutos)
          <input
            type="number"
            min={0}
            max={60}
            step={1}
            className="field mt-2"
            value={Number.isNaN(form.offset_min) ? '' : form.offset_min}
            onChange={(e) => edit({ offset_min: e.target.value === '' ? NaN : Number(e.target.value) })}
          />
          <span className="mt-1 block text-[11px] font-normal text-muted">Antes do fim do vazio ligar o diferido; depois do início, desligar (0–60).</span>
        </label>
        <div>
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold" id="limit-switch">
              Limite da bateria: {form.battery_limit} %
            </span>
            <button
              role="switch"
              aria-checked={form.battery_limit_enabled}
              aria-labelledby="limit-switch"
              onClick={() => edit({ battery_limit_enabled: !form.battery_limit_enabled })}
              className={`inline-flex h-6 w-11 items-center rounded-full p-0.5 transition-colors ${form.battery_limit_enabled ? 'bg-primary' : 'bg-line'}`}
            >
              <span className={`h-5 w-5 rounded-full bg-white shadow transition-transform ${form.battery_limit_enabled ? 'translate-x-5' : ''}`} />
            </button>
          </div>
          <input
            type="range"
            min={50}
            max={100}
            step={1}
            disabled={!form.battery_limit_enabled}
            className="mt-3 w-full accent-[var(--primary)] disabled:opacity-40"
            value={form.battery_limit}
            onChange={(e) => edit({ battery_limit: Number(e.target.value) })}
          />
          <span className="mt-1 block text-[11px] font-normal text-muted">
            {form.battery_limit_enabled
              ? `Aplica-se sempre em carga (em Casa e fora de Casa: só o limite de ${form.battery_limit} %).`
              : 'Limite desativado.'}
          </span>
        </div>
      </div>
      {!offsetOk && <div className="mt-2 text-xs text-danger-ink">O desvio tem de ser um número inteiro entre 0 e 60 minutos.</div>}
      <ErrorBox message={save.isError ? errorText(save.error) : null} />
      <div className="mt-3">
        <button className="btn btn-primary" disabled={!dirty || !offsetOk || save.isPending} onClick={() => save.mutate()}>
          {save.isPending && <span className="spinner" aria-hidden="true" />}
          Guardar
        </button>
      </div>
    </div>
  )
}

function TariffCard({ saved }: { saved: Tariff }) {
  const qc = useQueryClient()
  const [draft, setDraft] = useState<Tariff | null>(null)
  const [confirming, setConfirming] = useState(false)
  const [justSaved, setJustSaved] = useState(false)
  const tariff = draft ?? saved
  const dirty = draft !== null && JSON.stringify(draft) !== JSON.stringify(saved)
  const { year, month, day } = azoresNow()
  const today = `${year}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`

  const done = (data: Tariff) => {
    qc.setQueryData(['tariff'], data)
    setDraft(null)
    setJustSaved(true)
    void qc.invalidateQueries({ queryKey: ['status'] })
    void qc.invalidateQueries({ queryKey: ['events'] })
  }
  const save = useMutation({ mutationFn: () => apiSend<Tariff>('PUT', '/api/tariff', tariff), onSuccess: done })
  const reset = useMutation({ mutationFn: () => apiSend<Tariff>('POST', '/api/tariff/reset'), onSuccess: done })
  const edit = (t: Tariff) => {
    setJustSaved(false)
    save.reset()
    setDraft(t)
  }
  const error = save.isError ? errorText(save.error) : reset.isError ? errorText(reset.error) : null

  return (
    <>
      <div className="card">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <div className="label">Tarifário · {tariff.name}</div>
            <div className="text-[11px] text-muted">Horas de vazio, cheias e ponta por estação. Corrija aqui se a EDA as alterar.</div>
          </div>
          <div className="flex items-center gap-2">
            {dirty ? <Dirty /> : justSaved && <Saved />}
            {confirming ? (
              <span className="inline-flex items-center gap-2 rounded-[9px] bg-warn-soft px-3 py-[6px] text-xs font-semibold text-warn-ink">
                Repor o tarifário EDA?{dirty && ' As alterações por guardar perdem-se.'}
                <button
                  className="btn btn-primary !px-3 !py-1"
                  disabled={reset.isPending}
                  onClick={() => {
                    setConfirming(false)
                    reset.mutate()
                  }}
                >
                  Sim
                </button>
                <button className="btn !px-3 !py-1" onClick={() => setConfirming(false)}>
                  Não
                </button>
              </span>
            ) : (
              <button className="btn" disabled={reset.isPending} onClick={() => setConfirming(true)}>
                Repor EDA
              </button>
            )}
          </div>
        </div>
        <div className="mt-3">
          <TariffEditor tariff={tariff} onChange={edit} />
        </div>
      </div>

      <div className="card">
        <div className="label">Preços</div>
        <div className="mt-2">
          <PriceTable prices={tariff.prices} today={today} onChange={(prices) => edit({ ...tariff, prices })} />
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <button className="btn btn-primary" disabled={!dirty || save.isPending} onClick={() => save.mutate()}>
          {save.isPending && <span className="spinner" aria-hidden="true" />}
          Guardar tarifário
        </button>
        {dirty && (
          <button className="btn" onClick={() => setDraft(null)}>
            Descartar alterações
          </button>
        )}
        <ErrorBox message={error} />
      </div>
    </>
  )
}

export default function Automacao() {
  const automation = useQuery({ queryKey: ['automation'], queryFn: () => apiGet<Automation>('/api/automation') })
  const tariff = useQuery({ queryKey: ['tariff'], queryFn: () => apiGet<Tariff>('/api/tariff') })
  const events = useQuery({
    queryKey: ['events', 7],
    queryFn: () => apiGet<ScheduleEvent[]>('/api/tariff/events?days=7'),
    refetchInterval: 60_000,
  })
  const activity = useQuery({
    queryKey: ['activity', 100],
    queryFn: () => apiGet<Activity[]>('/api/activity?limit=100'),
    refetchInterval: 30_000,
  })

  return (
    <section className="grid gap-[14px]">
      <h1 className="text-xl font-bold">Automação</h1>
      {automation.isError && <div className="card text-danger-ink">Não foi possível obter as definições da automação.</div>}
      {automation.data && <SettingsCard saved={automation.data} />}
      {tariff.isError && <div className="card text-danger-ink">Não foi possível obter o tarifário.</div>}
      {tariff.data && <TariffCard saved={tariff.data} />}
      <div className="card">
        <div className="label">Próximos eventos · 7 dias</div>
        <EventsList events={events.data ?? []} />
      </div>
      <div className="card">
        <div className="label">Registo de atividade</div>
        <div className="max-h-96 overflow-y-auto">
          <ActivityList items={activity.data ?? []} />
        </div>
      </div>
    </section>
  )
}
