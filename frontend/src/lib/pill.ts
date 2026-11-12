import type { Activity, Status } from '../api'

export type Tone = 'ok' | 'info' | 'neutral' | 'error'
export interface PillState {
  label: string
  tone: Tone
}

/** Pill for the car's state; `activity` is newest-first. An error stays "unresolved" until a later entry follows it. */
export function pillState(status: Status, activity: Activity[] | undefined): PillState | null {
  const car = status.car
  if (!car) return null
  if (activity?.[0]?.kind === 'error') return { label: 'Erro de automação', tone: 'error' }
  if (car.charging) return { label: `A carregar · ${status.period}`, tone: 'ok' }
  if (car.plugged) {
    return status.last_command?.mode === 'defer' && !status.override
      ? { label: 'Em diferido', tone: 'info' }
      : { label: 'Ligado · sem carregar', tone: 'neutral' }
  }
  return { label: 'Desligado da tomada', tone: 'neutral' }
}
