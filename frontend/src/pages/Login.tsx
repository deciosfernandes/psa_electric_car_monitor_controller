import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { Navigate, useNavigate } from 'react-router'
import { apiGet, apiSend, type AuthState } from '../api'
import { Icon } from '../layout/Icon'

export default function Login() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const auth = useQuery({ queryKey: ['auth'], queryFn: () => apiGet<AuthState>('/api/auth/state') })
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  if (auth.isPending) return null
  if (auth.data?.logged_in) return <Navigate to="/" replace />
  const first = auth.data ? !auth.data.configured : false

  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await apiSend('POST', first ? '/api/auth/setup' : '/api/auth/login', { password })
      if (first) await apiSend('POST', '/api/auth/login', { password })
      await queryClient.invalidateQueries({ queryKey: ['auth'] })
      navigate('/', { replace: true })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Não foi possível iniciar sessão')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex min-h-full items-center justify-center p-6">
      <form onSubmit={submit} className="card w-full max-w-sm p-8" aria-labelledby="login-title">
        <div className="mb-6 flex items-center gap-2 text-lg font-extrabold">
          <span className="text-primary">
            <Icon name="bolt" size={22} />
          </span>
          PSACC
        </div>
        <h1 id="login-title" className="text-xl font-bold">
          {first ? 'Defina uma palavra-passe' : 'Entrar'}
        </h1>
        <p className="mb-5 mt-1 text-[13px] text-muted">
          {first
            ? 'Será pedida sempre que abrir a aplicação. Mínimo de 8 caracteres.'
            : 'Introduza a palavra-passe da aplicação.'}
        </p>
        <label htmlFor="password" className="label mb-1.5 block">
          Palavra-passe
        </label>
        <input
          id="password"
          type="password"
          className="field"
          autoComplete={first ? 'new-password' : 'current-password'}
          minLength={first ? 8 : undefined}
          maxLength={1024}
          required
          autoFocus
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          aria-describedby={error ? 'login-error' : undefined}
          aria-invalid={error ? true : undefined}
        />
        {error && (
          <p id="login-error" role="alert" className="mt-3 rounded-[9px] bg-danger-soft px-3 py-2 text-[13px] text-danger-ink">
            {error}
          </p>
        )}
        <button type="submit" className="btn btn-primary mt-5 w-full" disabled={busy || !password}>
          {first ? 'Definir palavra-passe' : 'Entrar'}
        </button>
      </form>
    </div>
  )
}
