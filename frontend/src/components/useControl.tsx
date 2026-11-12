import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useState, type ReactNode } from 'react'
import { apiSend } from '../api'

interface ControlResult {
  ok: boolean
  message: string
}
interface ToastState {
  message: string
  ok: boolean
}

/** Runs control commands: tracks which one is pending and shows the API `message` as a toast. */
export function useControl(): {
  run: (key: string, path: string, body?: unknown) => Promise<void>
  pending: string | null
  last: { key: string; ok: boolean; message: string } | null
  toast: ReactNode
} {
  const qc = useQueryClient()
  const [pending, setPending] = useState<string | null>(null)
  const [toast, setToast] = useState<ToastState | null>(null)
  const [last, setLast] = useState<{ key: string; ok: boolean; message: string } | null>(null)

  useEffect(() => {
    if (!toast) return
    const id = setTimeout(() => setToast(null), 5000)
    return () => clearTimeout(id)
  }, [toast])

  async function run(key: string, path: string, body?: unknown) {
    setPending(key)
    try {
      const res = await apiSend<ControlResult>('POST', path, body ?? {})
      setToast({ message: res.message, ok: res.ok })
      setLast({ key, ok: res.ok, message: res.message })
    } catch (e) {
      const message = e instanceof Error ? e.message : 'Erro inesperado'
      setToast({ message, ok: false })
      setLast({ key, ok: false, message })
    } finally {
      setPending(null)
      void qc.invalidateQueries({ queryKey: ['status'] })
      void qc.invalidateQueries({ queryKey: ['activity'] })
    }
  }

  const node = toast && (
    <div
      role="status"
      className={`fixed bottom-6 right-6 z-10 max-w-sm rounded-[10px] px-4 py-3 text-[13px] font-semibold shadow-lg ${
        toast.ok ? 'bg-ok-soft text-ok-ink' : 'bg-danger-soft text-danger-ink'
      }`}
    >
      {toast.message}
    </div>
  )
  return { run, pending, last, toast: node }
}
