import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { apiGet, apiSend } from '../api'

interface SetupState {
  psa_connected: boolean
  otp_ready: boolean
  vin: string | null
}
interface LoginResult {
  ok: boolean
  manual_url: string | null
  error: string | null
}

const APPS = ['MyPeugeot', 'MyCitroen', 'MyDS', 'MyOpel', 'MyVauxhall']
const errorText = (e: unknown) => (e instanceof Error ? e.message : 'Erro inesperado')

function Err({ message }: { message: string | null }) {
  return message ? (
    <div role="alert" className="mt-2 rounded-[9px] bg-danger-soft px-3 py-2 text-xs font-semibold text-danger-ink">
      {message}
    </div>
  ) : null
}

/** PSA account setup: credentials -> (manual code) -> SMS + PIN -> done. Secrets live only in local state. */
export function SetupWizard() {
  const qc = useQueryClient()
  const state = useQuery({ queryKey: ['setup-state'], queryFn: () => apiGet<SetupState>('/api/setup/state') })
  const [chosen, setStep] = useState<1 | 2 | 3 | null>(null)
  const [reconfigure, setReconfigure] = useState(false)
  const [manualUrl, setManualUrl] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)
  const [form, setForm] = useState({ app_name: 'MyPeugeot', email: '', password: '', country: 'PT' })
  const [code, setCode] = useState('')
  const [sms, setSms] = useState('')
  const [pin, setPin] = useState('')
  const [smsSent, setSmsSent] = useState(false)

  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ['setup-state'] })
    void qc.invalidateQueries({ queryKey: ['status'] })
  }

  const login = useMutation({
    mutationFn: () => apiSend<LoginResult>('POST', '/api/setup/login', form),
    onSuccess: (r) => {
      if (!r.ok && !r.manual_url) return // plain failure: stay on step 1 and show the error
      setForm((f) => ({ ...f, password: '' })) // never keep the PSA password around
      setManualUrl(r.manual_url)
      setNote(r.ok ? null : r.error)
      setStep(r.ok ? 3 : 2)
      refresh()
    },
  })
  const sendCode = useMutation({
    mutationFn: () => apiSend('POST', '/api/setup/code', { code: code.trim() }),
    onSuccess: () => {
      setCode('')
      setStep(3)
      refresh()
    },
  })
  const sendSms = useMutation({ mutationFn: () => apiSend('POST', '/api/setup/sms'), onSuccess: () => setSmsSent(true) })
  const otp = useMutation({
    mutationFn: () => apiSend('POST', '/api/setup/otp', { sms_code: sms.trim(), pin }),
    onSuccess: () => {
      setSms('')
      setPin('')
      setSmsSent(false)
      setReconfigure(false)
      setStep(1)
      refresh()
    },
  })

  const done = state.data?.psa_connected && state.data.otp_ready && !reconfigure
  // Already logged in but OTP missing (e.g. after a reload): resume at the SMS/PIN step
  const step = chosen ?? (state.data?.psa_connected && !state.data.otp_ready ? 3 : 1)
  const loginFailed = login.data && !login.data.ok && !login.data.manual_url ? (login.data.error ?? 'Falha no login') : null
  const safeUrl = manualUrl?.startsWith('https://') ? manualUrl : null
  const formOk = form.email.trim() !== '' && form.password !== ''

  if (state.isError) return <div className="text-danger-ink">Não foi possível obter o estado da conta PSA.</div>
  if (!state.data) return null

  if (done) {
    return (
      <div className="flex flex-wrap items-center gap-3">
        <span className="rounded-full bg-ok-soft px-2 py-0.5 text-[11px] font-semibold text-ok-ink">Ligada</span>
        <span className="text-[13px]">
          VIN: <span className="font-semibold">{state.data.vin ?? '—'}</span>
        </span>
        <button
          className="btn"
          onClick={() => {
            setReconfigure(true)
            setStep(1)
          }}
        >
          Refazer ligação
        </button>
      </div>
    )
  }

  return (
    <div>
      <ol className="mb-3 flex flex-wrap gap-2 text-[11px] font-semibold text-muted">
        {['1. Credenciais', '2. Código manual', '3. SMS e PIN'].map((t, i) => (
          <li key={t} className={step === i + 1 ? 'rounded-full bg-primary-soft px-2 py-0.5 text-primary-ink' : 'px-2 py-0.5'} aria-current={step === i + 1 ? 'step' : undefined}>
            {t}
          </li>
        ))}
      </ol>

      {step === 1 && (
        <form
          autoComplete="off"
          className="grid gap-3 md:grid-cols-2"
          onSubmit={(e) => {
            e.preventDefault()
            login.mutate()
          }}
        >
          <label className="block text-xs font-semibold">
            Aplicação
            <select className="field mt-1" value={form.app_name} onChange={(e) => setForm({ ...form, app_name: e.target.value })}>
              {APPS.map((a) => (
                <option key={a}>{a}</option>
              ))}
            </select>
          </label>
          <label className="block text-xs font-semibold">
            País
            <input className="field mt-1" maxLength={2} autoComplete="off" value={form.country} onChange={(e) => setForm({ ...form, country: e.target.value.toUpperCase() })} />
          </label>
          <label className="block text-xs font-semibold">
            Email da conta
            <input type="email" className="field mt-1" autoComplete="off" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
          </label>
          <label className="block text-xs font-semibold">
            Palavra-passe da conta
            <input type="password" className="field mt-1" autoComplete="new-password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} />
          </label>
          <div className="md:col-span-2">
            <button className="btn btn-primary" type="submit" disabled={!formOk || login.isPending}>
              {login.isPending && <span className="spinner" aria-hidden="true" />}
              Iniciar sessão
            </button>
            {reconfigure && (
              <button type="button" className="btn ml-2" onClick={() => setReconfigure(false)}>
                Cancelar
              </button>
            )}
            <Err message={login.isError ? errorText(login.error) : loginFailed} />
          </div>
        </form>
      )}

      {step === 2 && (
        <form
          autoComplete="off"
          className="grid gap-3"
          onSubmit={(e) => {
            e.preventDefault()
            sendCode.mutate()
          }}
        >
          <p className="text-[13px]">
            O login automático não foi possível{note ? ` (${note})` : ''}.{' '}
            {safeUrl && (
              <a href={safeUrl} target="_blank" rel="noopener noreferrer" className="font-semibold text-primary-ink underline">
                Abrir a página de login da PSA
              </a>
            )}{' '}
            num novo separador, inicie sessão e cole abaixo o código que obtiver.
          </p>
          <label className="block text-xs font-semibold">
            Código
            <input className="field mt-1" autoComplete="off" value={code} onChange={(e) => setCode(e.target.value)} />
          </label>
          <div>
            <button className="btn btn-primary" type="submit" disabled={code.trim() === '' || sendCode.isPending}>
              {sendCode.isPending && <span className="spinner" aria-hidden="true" />}
              Confirmar código
            </button>
            <button type="button" className="btn ml-2" onClick={() => setStep(1)}>
              Voltar
            </button>
            <Err message={sendCode.isError ? errorText(sendCode.error) : null} />
          </div>
        </form>
      )}

      {step === 3 && (
        <form
          autoComplete="off"
          className="grid gap-3 md:grid-cols-2"
          onSubmit={(e) => {
            e.preventDefault()
            otp.mutate()
          }}
        >
          <div className="md:col-span-2">
            <button type="button" className="btn" disabled={sendSms.isPending} onClick={() => sendSms.mutate()}>
              {sendSms.isPending && <span className="spinner" aria-hidden="true" />}
              {smsSent ? 'Reenviar SMS' : 'Enviar SMS'}
            </button>
            {smsSent && <span className="ml-2 text-xs font-semibold text-ok-ink">SMS pedido</span>}
            <Err message={sendSms.isError ? errorText(sendSms.error) : null} />
          </div>
          <label className="block text-xs font-semibold">
            Código SMS
            <input className="field mt-1" inputMode="numeric" autoComplete="off" value={sms} onChange={(e) => setSms(e.target.value)} />
          </label>
          <label className="block text-xs font-semibold">
            PIN da aplicação
            <input type="password" className="field mt-1" inputMode="numeric" autoComplete="off" value={pin} onChange={(e) => setPin(e.target.value)} />
          </label>
          <div className="md:col-span-2">
            <button className="btn btn-primary" type="submit" disabled={sms.trim() === '' || pin === '' || otp.isPending}>
              {otp.isPending && <span className="spinner" aria-hidden="true" />}
              Ativar
            </button>
            <button type="button" className="btn ml-2" onClick={() => setStep(safeUrl ? 2 : 1)}>
              Voltar
            </button>
            <Err message={otp.isError ? errorText(otp.error) : null} />
          </div>
        </form>
      )}
    </div>
  )
}
