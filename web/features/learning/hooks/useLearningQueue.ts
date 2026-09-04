'use client'

import { useCallback, useEffect, useState } from 'react'

import { fetchLearningQueue } from '@/lib/learning-coordinator-api'
import type { LearningQueueItem } from '../model'

export function useLearningQueue(sessionId?: string) {
  const [items, setItems] = useState<LearningQueueItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<Error | null>(null)

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const next = await fetchLearningQueue(sessionId)
      setItems(next)
      setError(null)
    } catch (reason) {
      setError(reason instanceof Error ? reason : new Error(String(reason)))
    } finally {
      setLoading(false)
    }
  }, [sessionId])

  useEffect(() => {
    void refresh()
  }, [refresh])

  return { items, loading, error, refresh }
}
