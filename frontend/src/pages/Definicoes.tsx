import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { apiGet, apiSend } from '../api'
import { SetupWizard } from '../components/SetupWizard'

interface Settings {
  discord_webhook_set: boolean
  discord_info: boolean
  refresh_min: number
}

const errorText = (e: unknown) => (e instanceof Error ? e.message : 'Erro inesperado')
const Saved = () => <span className="text-[11px] font-semibold text-ok-ink">Guardado</span>

function Err({ message }: { message: string | null }) {
  return message ? (
    <div role="alert" className="mt-2 rounded-[9px] bg-danger-soft px-3 py-2 text-xs font-semibold text-danger-ink">
      {message}
    </div>
  ) : null
}

function DiscordCard({ saved }: { saved: Settings }) {
  const qc = useQueryClient()
  const [webhook, setWebhook] = useState('') // never pre-filled: the backend does not return it
  const [replacing, setReplacing] = useState(false)
  const [info, setInfo] = useState(saved.discord_info)
  const [justSaved, setJustSaved] = useState(false)
  const showInput = !saved.discord_webhook_set || replacing
  const save = useMutation({
    mutationFn: (v: { webhook?: string }) =>
      apiSend<Settings>('PUT', '/api/settings', { discord_webhook: v.webhook, discord_info: info, refresh_min: saved.refresh_min }),
    onSuccess: (d) => {
      qc.setQueryData(['settings'], d)
      setWebhook('')
      setReplacing(false)
      setJustSaved(true)
    },
  })
  const test = useMutation({ mutationFn: () => apiSend<{ ok: boolean; message: string }>('POST', '/api/settings/discord-test') })
  const webhookInvalid = webhook !== '' && !webhook.startsWith('https://')

  return (
    <div className="card">
      <div className="flex items-center justify-between">
        <div className="label">Discord</div>
        {justSaved && <Saved />}
      </div>
      <div className="mt-3 grid gap-3">
        {showInput ? (
          <form
            autoComplete="off"
            className="flex flex-wrap items-end gap-2"
            onSubmit={(e) => {
              e.preventDefault()
              save.mutate({ webhook })
            }}
          >
            <label className="block min-w-[260px] flex-1 text-xs font-semibold">
              URL do webhook
              <input type="password" className="field mt-1" autoComplete="off" placeholder="https://discord.com/api/webhooks/…" value={webhook} onChange={(e) => setWebhook(e.target.value)} />
            </label>
            <button className="btn btn-primary" type="submit" disabled={webhook === '' || webhookInvalid || save.isPending}>
              Guardar webhook
            </button>
            {saved.discord_webhook_set && (
              <button
                type="button"
                className="btn"
                onClick={() => {
                  setReplacing(false)
                  setWebhook('')
                }}
              >
                Cancelar
              </button>
            )}
          </form>
        ) : (
          <div className="flex flex-wrap items-center gap-2">
            <span className="rounded-full bg-ok-soft px-2 py-0.5 text-[11px] font-semibold text-ok-ink">Webhook configurado</span>
            <button className="btn" onClick={() => setReplacing(true)}>
              Substituir
            </button>
            <button className="btn" disabled={save.isPending} onClick={() => save.mutate({ webhook: '' })}>
              Remover
            </button>
          </div>
        )}
        {webhookInvalid && <div className="text-xs text-danger-ink">O webhook tem de começar por https://</div>}
        <label className="flex items-center gap-2 text-[13px]">
          <input
            type="checkbox"
            checked={info}
            onChange={(e) => {
              setInfo(e.target.checked)
              setJustSaved(false)
            }}
          />
          Enviar mensagens informativas (início e fim de carga)
        </label>
        <div className="flex flex-wrap items-center gap-2">
          <button className="btn btn-primary" disabled={info === saved.discord_info || save.isPending} onClick={() => save.mutate({})}>
            Guardar
          </button>
          <button className="btn" disabled={!saved.discord_webhook_set || test.isPending} onClick={() => test.mutate()}>
            {test.isPending && <span className="spinner" aria-hidden="true" />}
            Enviar teste
          </button>
          {test.data && (
            <span role="status" className={`text-xs font-semibold ${test.data.ok ? 'text-ok-ink' : 'text-danger-ink'}`}>
              {test.data.ok ? `Confirmado · ${test.data.message}` : `Falhou: ${test.data.message}`}
            </span>
          )}
        </div>
        <Err message={save.isError ? errorText(save.error) : test.isError ? errorText(test.error) : null} />
      </div>
    </div>
  )
}

function RefreshCard({ saved }: { saved: Settings }) {
  const qc = useQueryClient()
  const [minutes, setMinutes] = useState<number>(saved.refresh_min)
  const [justSaved, setJustSaved] = useState(false)
  const ok = Number.isInteger(minutes) && minutes >= 1 && minutes <= 60
  const save = useMutation({
    mutationFn: () => apiSend<Settings>('PUT', '/api/settings', { discord_info: saved.discord_info, refresh_min: minutes }),
    onSuccess: (d) => {
      qc.setQueryData(['settings'], d)
      setJustSaved(true)
    },
  })
  return (
    <div className="card">
      <div className="flex items-center justify-between">
        <div className="label">Atualização</div>
        {justSaved && <Saved />}
      </div>
      <div className="mt-3 flex flex-wrap items-end gap-2">
        <label className="block text-xs font-semibold">
          Intervalo de atualização (minutos)
          <input
            type="number"
            min={1}
            max={60}
            step={1}
            className="field mt-1 !w-40"
            value={Number.isNaN(minutes) ? '' : minutes}
            onChange={(e) => {
              setJustSaved(false)
              setMinutes(e.target.value === '' ? NaN : Number(e.target.value))
            }}
          />
        </label>
        <button className="btn btn-primary" disabled={!ok || minutes === saved.refresh_min || save.isPending} onClick={() => save.mutate()}>
          Guardar
        </button>
      </div>
      {!ok && <div className="mt-2 text-xs text-danger-ink">Indique um número inteiro entre 1 e 60.</div>}
      <Err message={save.isError ? errorText(save.error) : null} />
    </div>
  )
}

function PasswordCard() {
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [again, setAgain] = useState('')
  const [justSaved, setJustSaved] = useState(false)
  const save = useMutation({
    mutationFn: () => apiSend('POST', '/api/auth/password', { current, new: next }),
    onSuccess: () => {
      setCurrent('')
      setNext('')
      setAgain('')
      setJustSaved(true)
    },
  })
  const mismatch = again !== '' && again !== next
  const valid = current !== '' && next.length >= 8 && next.length <= 1024 && next === again
  return (
    <form
      className="card"
      autoComplete="off"
      onSubmit={(e) => {
        e.preventDefault()
        setJustSaved(false)
        save.mutate()
      }}
    >
      <div className="flex items-center justify-between">
        <div className="label">Palavra-passe</div>
        {justSaved && <Saved />}
      </div>
      <div className="mt-3 grid gap-3 md:grid-cols-3">
        <label className="block text-xs font-semibold">
          Atual
          <input type="password" className="field mt-1" autoComplete="current-password" maxLength={1024} value={current} onChange={(e) => setCurrent(e.target.value)} />
        </label>
        <label className="block text-xs font-semibold">
          Nova (mínimo 8 caracteres)
          <input type="password" className="field mt-1" autoComplete="new-password" maxLength={1024} value={next} onChange={(e) => setNext(e.target.value)} />
        </label>
        <label className="block text-xs font-semibold">
          Repetir a nova
          <input type="password" className="field mt-1" autoComplete="new-password" maxLength={1024} value={again} onChange={(e) => setAgain(e.target.value)} />
        </label>
      </div>
      {mismatch && <div className="mt-2 text-xs text-danger-ink">As palavras-passe não coincidem.</div>}
      <Err message={save.isError ? errorText(save.error) : null} />
      <div className="mt-3">
        <button className="btn btn-primary" type="submit" disabled={!valid || save.isPending}>
          {save.isPending && <span className="spinner" aria-hidden="true" />}
          Alterar palavra-passe
        </button>
      </div>
    </form>
  )
}

export default function Definicoes() {
  const settings = useQuery({ queryKey: ['settings'], queryFn: () => apiGet<Settings>('/api/settings') })
  return (
    <section className="grid gap-[14px]">
      <h1 className="text-xl font-bold">Definições</h1>
      <div className="card">
        <div className="label mb-3">Conta PSA</div>
        <SetupWizard />
      </div>
      {settings.isError && <div className="card text-danger-ink">Não foi possível obter as definições.</div>}
      {settings.data && (
        <>
          <DiscordCard saved={settings.data} />
          <RefreshCard saved={settings.data} />
        </>
      )}
      <PasswordCard />
    </section>
  )
}
