import type { PillState, Tone } from '../lib/pill'

const TONE: Record<Tone, string> = {
  ok: 'bg-ok-soft text-ok-ink',
  info: 'bg-primary-soft text-primary-ink',
  neutral: 'bg-line text-ink',
  error: 'bg-danger-soft text-danger-ink',
}

export function StatusPill({ state }: { state: PillState }) {
  return (
    <span role="status" className={`inline-block rounded-full px-[10px] py-1 text-[11px] font-semibold ${TONE[state.tone]}`}>
      ● {state.label}
    </span>
  )
}
