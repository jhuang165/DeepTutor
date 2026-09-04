import assert from 'node:assert/strict'
import test from 'node:test'

import {
  approveLearningPath,
  fetchLearningQueue,
  removeLearningEvidence,
} from '../lib/learning-coordinator-api'
import type { LearningPathDraft } from '../features/learning/model'

function mockFetchJson(body: unknown): {
  calls: Array<[RequestInfo | URL, RequestInit | undefined]>
  restore: () => void
} {
  const original = globalThis.fetch
  const calls: Array<[RequestInfo | URL, RequestInit | undefined]> = []
  globalThis.fetch = async (input, init) => {
    calls.push([input, init])
    return Response.json(body)
  }
  return {
    calls,
    restore: () => {
      globalThis.fetch = original
    },
  }
}

test('encodes session id when loading the queue', async () => {
  // Break caught: sending an unencoded session id changes the queue request identity.
  const mock = mockFetchJson({ items: [] })
  try {
    await fetchLearningQueue('session / one')
    assert.match(String(mock.calls[0]?.[0]), /session_id=session\+%2F\+one/)
  } finally {
    mock.restore()
  }
})

test('uses DELETE for evidence removal', async () => {
  // Break caught: evidence deletion accidentally falls back to a safe GET request.
  const mock = mockFetchJson({ evidence: [] })
  try {
    await removeLearningEvidence('ev one')
    assert.match(String(mock.calls[0]?.[0]), /ev%20one$/)
    assert.equal(mock.calls[0]?.[1]?.method, 'DELETE')
  } finally {
    mock.restore()
  }
})

test('sends the edited learning preferences when approving a path', async () => {
  // Break caught: approval drops learner-edited context before the server validates the draft.
  let body: Record<string, unknown> | undefined
  const original = globalThis.fetch
  globalThis.fetch = async (_input, init) => {
    body = JSON.parse(String(init?.body)) as Record<string, unknown>
    return Response.json({ path_id: 'path-1' })
  }
  const draft: LearningPathDraft = {
    path_id: 'draft-1',
    name: 'Signals',
    goal: 'Understand signals',
    description: 'A route',
    starting_point: 'I can sketch sine waves.',
    teaching_preferences: 'Use visual examples first.',
    sources: [],
    modules: [
      {
        id: 'module-1',
        name: 'Foundation',
        order: 0,
        pass_threshold: 0.7,
        knowledge_points: [
          { id: 'objective-1', name: 'Frequency', type: 'concept', module_id: 'module-1' },
        ],
      },
    ],
  }
  try {
    await approveLearningPath('thread-1', draft)
    assert.equal(body?.starting_point, 'I can sketch sine waves.')
    assert.equal(body?.teaching_preferences, 'Use visual examples first.')
  } finally {
    globalThis.fetch = original
  }
})
