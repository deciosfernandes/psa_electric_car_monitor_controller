import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Sidebar } from './Sidebar'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('Sidebar', () => {
  it('logout posts to the API and goes to the login page', async () => {
    const fetchMock = vi.fn(async (path: string) =>
      new Response(JSON.stringify(path === '/api/auth/logout' ? { ok: true } : {}), { status: 200 }),
    )
    vi.stubGlobal('fetch', fetchMock)
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter initialEntries={['/']}>
          <Routes>
            <Route path="/" element={<Sidebar />} />
            <Route path="/login" element={<div>página de login</div>} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    )
    fireEvent.click(screen.getByRole('button', { name: 'Terminar sessão' }))
    expect(await screen.findByText('página de login')).toBeTruthy()
    await waitFor(() => {
      const call = fetchMock.mock.calls.find(([p]) => p === '/api/auth/logout') as unknown as [string, RequestInit]
      expect(call?.[1]?.method).toBe('POST')
    })
  })
})
