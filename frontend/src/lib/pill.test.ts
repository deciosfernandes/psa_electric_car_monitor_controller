import { describe, expect, it } from 'vitest'
import type { Activity, CarSnapshot, Status } from '../api'
import { pillState } from './pill'

const car: CarSnapshot = {
  vin: 'X', at: '2026-11-12T00:00:00-01:00', soc: 50, range_km: 100, plugged: true, charging: false,
  charge_status: 'Stopped', charge_rate_kmh: 0, next_delayed_time: null, latitude: null, longitude: null,
  locked: true, precond_on: false, mileage_km: null, battery_health: null,
}
const status = (over: Partial<Status> = {}, c: Partial<CarSnapshot> = {}): Status => ({
  car: { ...car, ...c }, where: 'home', place_name: null, mileage_km: null, position_age_min: null, period: 'vazio', next_event: null,
  automation: { enabled: true, offset_min: 10, battery_limit: 80 },
  override: null, last_command: null, mqtt_connected: true, psa_connected: true, updated_at: '2026-11-12T00:00:00-01:00', ...over,
})
const act = (kind: Activity['kind'], source: Activity['source'] = 'automation'): Activity[] => [
  { at: '2026-11-12T00:00:00-01:00', kind, message: 'x', source },
]
const deferred = { mode: 'defer' as const, at: '', verified: true, attempts: 1, reason: '', alerted: false, program_hour: [0, 10] as [number, number] }

describe('pillState', () => {
  it('charging', () => expect(pillState(status({}, { charging: true }), [])).toEqual({ label: 'A carregar · vazio', tone: 'ok' }))
  it('deferred', () => expect(pillState(status({ last_command: deferred }), [])).toEqual({ label: 'Em diferido', tone: 'info' }))
  it('plugged but idle', () => expect(pillState(status(), [])?.label).toBe('Ligado · sem carregar'))
  it('unplugged', () => expect(pillState(status({}, { plugged: false }), [])).toEqual({ label: 'Desligado da tomada', tone: 'neutral' }))
  it('latest activity is an error', () => expect(pillState(status({}, { charging: true }), act('error'))).toEqual({ label: 'Erro de automação', tone: 'error' }))
  it('error resolved by a later entry', () => expect(pillState(status(), [...act('action'), ...act('error')])?.tone).toBe('neutral'))
  it('control command error is not an automation error', () =>
    expect(pillState(status({}, { charging: true }), act('error', 'control'))?.label).toBe('A carregar · vazio'))
  it('automation error stays when only control entries follow', () =>
    expect(pillState(status(), [...act('action', 'control'), ...act('error')])?.label).toBe('Erro de automação'))
  it('no car', () => expect(pillState(status({ car: null }), [])).toBeNull())
})
