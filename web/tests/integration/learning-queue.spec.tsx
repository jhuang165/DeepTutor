import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

const { fetchLearningQueue } = vi.hoisted(() => ({
  fetchLearningQueue: vi.fn(),
}))

vi.mock('@/lib/learning-coordinator-api', () => ({ fetchLearningQueue }))

import { useLearningQueue } from '@/features/learning/hooks/useLearningQueue'
import type { LearningQueueItem } from '@/features/learning/model'

const item = (threadId: string): LearningQueueItem => ({
  thread_id: threadId,
  path_id: 'path-1',
  objective_id: 'objective-1',
  activity: {},
  reason: 'continue_path',
  reason_data: { objective: '', goal: '', path_name: 'Path one', answer_state: '' },
  priority: 1,
  due_at: null,
})

function deferred<T>() {
  let resolve: (value: T) => void = () => undefined
  let reject: (reason?: unknown) => void = () => undefined
  const promise = new Promise<T>((nextResolve, nextReject) => {
    resolve = nextResolve
    reject = nextReject
  })
  return { promise, reject, resolve }
}

afterEach(() => {
  fetchLearningQueue.mockReset()
})

describe('useLearningQueue', () => {
  it('keeps the newest session queue when an older request resolves late', async () => {
    // Break caught: an obsolete session request overwrites the queue for the current learner session.
    const first = deferred<LearningQueueItem[]>()
    const second = deferred<LearningQueueItem[]>()
    fetchLearningQueue.mockImplementation((sessionId?: string) =>
      sessionId === 'first' ? first.promise : second.promise
    )
    const { result, rerender } = renderHook(({ sessionId }) => useLearningQueue(sessionId), {
      initialProps: { sessionId: 'first' },
    })
    rerender({ sessionId: 'second' })
    await act(async () => {
      second.resolve([item('second')])
      await second.promise
    })
    await waitFor(() => expect(result.current.items[0]?.thread_id).toBe('second'))
    await act(async () => {
      first.resolve([item('first')])
      await first.promise
    })
    expect(result.current.items[0]?.thread_id).toBe('second')
  })

  it('clears stale items when the current session refresh fails', async () => {
    // Break caught: an error presents a prior session's learning card as if it were current.
    fetchLearningQueue
      .mockResolvedValueOnce([item('first')])
      .mockRejectedValueOnce(new Error('offline'))
    const { result, rerender } = renderHook(({ sessionId }) => useLearningQueue(sessionId), {
      initialProps: { sessionId: 'first' },
    })
    await waitFor(() => expect(result.current.items[0]?.thread_id).toBe('first'))
    rerender({ sessionId: 'second' })
    await waitFor(() => expect(result.current.error?.message).toBe('offline'))
    expect(result.current.items).toEqual([])
  })
})
