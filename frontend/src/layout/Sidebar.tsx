import { NavLink } from 'react-router'
import { useStatus } from '../useStatus'
import { Icon, type IconName } from './Icon'

const NAV: { to: string; label: string; icon: IconName }[] = [
  { to: '/', label: 'Painel', icon: 'painel' },
  { to: '/automacao', label: 'Automação', icon: 'automacao' },
  { to: '/cargas', label: 'Cargas', icon: 'cargas' },
  { to: '/viagens', label: 'Viagens', icon: 'viagens' },
  { to: '/consumo', label: 'Consumo', icon: 'consumo' },
  { to: '/controlo', label: 'Controlo', icon: 'controlo' },
  { to: '/definicoes', label: 'Definições', icon: 'definicoes' },
]

function minutesAgo(iso: string): number {
  return Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 60000))
}

function StatusFooter() {
  const { data } = useStatus()
  const since = data ? (data.car?.at ?? data.updated_at) : null
  return (
    <div className="mt-auto px-3 pt-4 text-[11px] leading-relaxed text-muted" aria-live="polite">
      <div>{since ? `Atualizado há ${minutesAgo(since)} min` : 'A atualizar…'}</div>
      {data && (
        <div className="flex items-center gap-1.5">
          <span
            className={`inline-block size-2 rounded-full ${data.mqtt_connected ? 'bg-vazio' : 'bg-ponta'}`}
            aria-hidden="true"
          />
          MQTT {data.mqtt_connected ? 'ligado' : 'desligado'}
        </div>
      )}
    </div>
  )
}

export function Sidebar() {
  return (
    <aside className="sticky top-0 flex h-screen w-[200px] shrink-0 flex-col border-r border-line bg-card px-3 py-[18px]">
      <div className="flex items-center gap-2 px-3 pb-4 pt-1 text-base font-extrabold">
        <span className="text-primary">
          <Icon name="bolt" size={20} />
        </span>
        PSACC
      </div>
      <nav aria-label="Principal" className="flex flex-col gap-0.5">
        {NAV.map(({ to, label, icon }) => (
          <NavLink
            key={to}
            to={to}
            end={to === '/'}
            className={({ isActive }) =>
              `flex items-center gap-2.5 rounded-[9px] px-3 py-[9px] text-[13px] no-underline transition-colors ${
                isActive ? 'bg-primary-soft font-semibold text-primary-ink' : 'text-muted hover:bg-bg hover:text-ink'
              }`
            }
          >
            <Icon name={icon} />
            {label}
          </NavLink>
        ))}
      </nav>
      <StatusFooter />
    </aside>
  )
}
