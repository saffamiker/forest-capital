/**
 * frontend/src/lib/useDiversificationData.ts
 *
 * One hook per /api/v1/analytics/* endpoint shipped in a239843.
 * Each hook returns { data, loading, error }. The backend reads
 * from analytics_metrics_cache (migration 028) so these requests
 * are sub-millisecond on the hot path and fall back to inline
 * compute on cold cache.
 *
 * NO module-level cache here on purpose — these payloads are
 * keyed off the current data_hash. A page refresh / data-hash
 * change should re-fetch. The endpoints are cheap on hot path so
 * a per-mount fetch is fine; consumers can lift state up if they
 * need cross-component sharing.
 */
import { useEffect, useState } from 'react'
import axios from 'axios'
import type {
  CorrelationMatrixPayload, TailRiskPayload, CapturePayload,
  DrawdownDurationPayload, CrisisPerformancePayload,
  RiskContributionPayload, DistributionPayload,
} from '../types/diversification'

export interface FetchState<T> {
  data: T | null
  loading: boolean
  error: string | null
}

function useEndpoint<T>(url: string): FetchState<T> {
  const [data, setData] = useState<T | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    axios.get<T>(url)
      .then((res) => {
        if (!cancelled) {
          setData(res.data)
          setError(null)
        }
      })
      .catch((err) => {
        if (!cancelled) {
          const detail = axios.isAxiosError(err)
            ? err.response?.data?.detail ?? err.message
            : 'fetch failed'
          setError(String(detail))
        }
      })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [url])

  return { data, loading, error }
}

export function useCorrelationMatrices(): FetchState<CorrelationMatrixPayload> {
  return useEndpoint<CorrelationMatrixPayload>('/api/v1/analytics/correlation')
}
export function useTailRisk(): FetchState<TailRiskPayload> {
  return useEndpoint<TailRiskPayload>('/api/v1/analytics/tail-risk')
}
export function useCaptureRatios(): FetchState<CapturePayload> {
  return useEndpoint<CapturePayload>('/api/v1/analytics/capture-ratios')
}
export function useDrawdownDuration(): FetchState<DrawdownDurationPayload> {
  return useEndpoint<DrawdownDurationPayload>('/api/v1/analytics/drawdown-duration')
}
export function useCrisisPerformance(): FetchState<CrisisPerformancePayload> {
  return useEndpoint<CrisisPerformancePayload>('/api/v1/analytics/crisis-performance')
}
export function useRiskContribution(): FetchState<RiskContributionPayload> {
  return useEndpoint<RiskContributionPayload>('/api/v1/analytics/risk-contribution')
}
export function useDistribution(): FetchState<DistributionPayload> {
  return useEndpoint<DistributionPayload>('/api/v1/analytics/distribution')
}
