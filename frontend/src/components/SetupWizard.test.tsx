import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { SetupWizard } from './SetupWizard'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function mockApi(state: object, login?: object) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (path: string) => {
      const body = path === '/api/setup/state' ? state : (login ?? { ok: true })
      return new Response(JSON.stringify(body), { status: 200 })
    }),
  )
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <SetupWizard />
    </QueryClientProvider>,
  )
}

const notConnected = { psa_connected: false, otp_ready: false, vin: null }

describe('SetupWizard', () => {
  it('failed headless login with manual_url moves to the manual code step', async () => {
    mockApi(notConnected, { ok: false, manual_url: 'https://idp.example/auth?x=1', error: 'login automático falhou (X)' })
    fireEvent.change(await screen.findByLabelText('Email da conta'), { target: { value: 'a@b.pt' } })
    fireEvent.change(screen.getByLabelText('Palavra-passe da conta'), { target: { value: 'pw' } })
    fireEvent.click(screen.getByRole('button', { name: 'Iniciar sessão' }))
    const link = await screen.findByRole('link', { name: /Abrir a página de login/ })
    expect(link.getAttribute('href')).toBe('https://idp.example/auth?x=1')
    expect(link.getAttribute('rel')).toBe('noopener noreferrer')
    expect(link.getAttribute('target')).toBe('_blank')
    expect(screen.getByLabelText('Código')).toBeTruthy()
  })

  it('does not render a non-https manual link', async () => {
    mockApi(notConnected, { ok: false, manual_url: 'javascript:alert(1)', error: 'x' })
    fireEvent.change(await screen.findByLabelText('Email da conta'), { target: { value: 'a@b.pt' } })
    fireEvent.change(screen.getByLabelText('Palavra-passe da conta'), { target: { value: 'pw' } })
    fireEvent.click(screen.getByRole('button', { name: 'Iniciar sessão' }))
    await screen.findByLabelText('Código')
    expect(screen.queryByRole('link')).toBeNull()
  })

  it('plain login failure stays on step 1 with the error', async () => {
    mockApi(notConnected, { ok: false, manual_url: null, error: 'credenciais inválidas' })
    fireEvent.change(await screen.findByLabelText('Email da conta'), { target: { value: 'a@b.pt' } })
    fireEvent.change(screen.getByLabelText('Palavra-passe da conta'), { target: { value: 'pw' } })
    fireEvent.click(screen.getByRole('button', { name: 'Iniciar sessão' }))
    expect((await screen.findByRole('alert')).textContent).toContain('credenciais inválidas')
    expect(screen.queryByLabelText('Código')).toBeNull()
  })

  it('resumes at SMS/PIN when connected without OTP', async () => {
    mockApi({ psa_connected: true, otp_ready: false, vin: 'V1' })
    expect(await screen.findByRole('button', { name: 'Enviar SMS' })).toBeTruthy()
  })

  it('shows VIN and Refazer ligação when fully set up', async () => {
    mockApi({ psa_connected: true, otp_ready: true, vin: 'VF3TESTVIN' })
    expect(await screen.findByText('VF3TESTVIN')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Refazer ligação' }))
    await waitFor(() => expect(screen.getByLabelText('Email da conta')).toBeTruthy())
  })
})
