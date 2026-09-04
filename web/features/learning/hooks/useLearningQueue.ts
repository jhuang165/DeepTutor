'use client'

import { useCallback, useEffect, useRef, useState } from 'react'

import { fetchLearningQueue } from '@/lib/learning-coordinator-api'
import type { LearningQueueItem } from '../model'

export function useLearningQueue(sessionId?: string) {
  const [items, setItems] = useState<LearningQueueItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<Error | null>(null)
  const requestGeneration = useRef(0)

  const refresh = useCallback(async () => {
    const generation = ++requestGeneration.current
    setLoading(true)
    try {
      const next = await fetchLearningQueue(sessionId)
      if (generation !== requestGeneration.current) return
      setItems(next)
      setError(null)
    } catch (reason) {
      if (generation !== requestGeneration.current) return
      setItems([])
      setError(reason instanceof Error ? reason : new Error(String(reason)))
    } finally {
      if (generation === requestGeneration.current) setLoading(false)
    }
  }, [sessionId])

  useEffect(() => {
    void refresh()
    return () => {
      requestGeneration.current += 1
    }
  }, [refresh])

  return { items, loading, error, refresh }
}
