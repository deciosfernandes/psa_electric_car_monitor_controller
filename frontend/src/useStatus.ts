import { useQuery } from '@tanstack/react-query'
import { apiGet, type Status } from './api'

export const useStatus = () =>
  useQuery({ queryKey: ['status'], queryFn: () => apiGet<Status>('/api/status'), refetchInterval: 30_000 })
