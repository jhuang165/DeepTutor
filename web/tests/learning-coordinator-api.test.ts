import assert from 'node:assert/strict'
import test from 'node:test'

import { fetchLearningQueue, removeLearningEvidence } from '../lib/learning-coordinator-api'

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
