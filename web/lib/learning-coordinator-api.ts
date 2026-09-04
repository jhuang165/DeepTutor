import type { components } from '@/contracts/generated/api'
import { apiFetch, apiUrl } from '@/lib/api'
import type {
  LearningEvidence,
  LearningPathDraft,
  LearningQueueItem,
  LearningThread,
} from '@/features/learning/model'

type EvidenceResponse = components['schemas']['EvidenceListResponse']

async function responseJson<T>(response: Response, message: string): Promise<T> {
  if (!response.ok) throw new Error(`${message}: ${response.status}`)
  return (await response.json()) as T
}

export async function fetchLearningQueue(sessionId = ''): Promise<LearningQueueItem[]> {
  const query = new URLSearchParams()
  if (sessionId) query.set('session_id', sessionId)
  const response = await apiFetch(apiUrl(`/api/learning/queue?${query}`))
  const body = await responseJson<{ items: readonly LearningQueueItem[] }>(
    response,
    'Failed to load learning queue'
  )
  return [...body.items]
}

export async function fetchLearningThread(threadId: string): Promise<LearningThread> {
  const response = await apiFetch(apiUrl(`/api/learning/threads/${encodeURIComponent(threadId)}`))
  return (
    await responseJson<{ thread: LearningThread }>(response, 'Failed to load learning thread')
  ).thread
}

export async function fetchLearningEvidence(threadId: string): Promise<LearningEvidence[]> {
  const response = await apiFetch(
    apiUrl(`/api/learning/threads/${encodeURIComponent(threadId)}/evidence`)
  )
  const body = await responseJson<EvidenceResponse>(response, 'Failed to load learning evidence')
  return [...body.evidence]
}

export async function removeLearningEvidence(evidenceId: string): Promise<LearningEvidence[]> {
  const response = await apiFetch(
    apiUrl(`/api/learning/evidence/${encodeURIComponent(evidenceId)}`),
    { method: 'DELETE' }
  )
  const body = await responseJson<EvidenceResponse>(response, 'Failed to remove learning evidence')
  return [...body.evidence]
}

export async function approveLearningPath(
  threadId: string,
  draft: LearningPathDraft
): Promise<string> {
  const response = await apiFetch(
    apiUrl(`/api/learning/threads/${encodeURIComponent(threadId)}/approve-path`),
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name: draft.name,
        goal: draft.goal,
        description: draft.description,
        starting_point: draft.starting_point,
        teaching_preferences: draft.teaching_preferences,
        emoji: '🧭',
        sources: draft.sources,
        modules: draft.modules,
      }),
    }
  )
  return (await responseJson<{ path_id: string }>(response, 'Failed to approve learning path'))
    .path_id
}

export async function setLearningHelpLevel(
  threadId: string,
  helpLevel: number
): Promise<LearningThread> {
  const response = await apiFetch(
    apiUrl(`/api/learning/threads/${encodeURIComponent(threadId)}/help`),
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ help_level: helpLevel }),
    }
  )
  return (await responseJson<{ thread: LearningThread }>(response, 'Failed to update help level'))
    .thread
}
