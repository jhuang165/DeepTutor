import assert from 'node:assert/strict'
import test from 'node:test'

import {
  parseLearningDecision,
  parseLearningPathProposal,
  selectLearningDecision,
  selectLearningPathProposal,
} from '../features/learning/model'
import type { MessageItem } from '../features/chat/ChatStateAdapter'

const decision = {
  scope: 'lesson',
  route: 'mastery_path',
  goal: 'Understand Fourier transforms',
  language: 'en',
  thread_id: 'thread-1',
  objective_id: 'objective-1',
  activity: {
    kind: 'guided_attempt',
    objective: 'Relate frequency to a signal',
    learner_action: 'Predict the dominant frequency.',
    knowledge_type: 'concept',
    recipe_id: 'concept-transfer',
    recipe_version: 1,
    recipe_step: 0,
    help_level: 2,
    source_refs: [],
    assessment_method: 'rubric',
    independent_required: true,
    transfer_required: false,
    next_action: 'Answer the prompt',
  },
  reason: 'A short prediction checks the current mental model.',
  confidence: 0.8,
  requires_approval: false,
  source_policy: 'open',
  requested_capability: 'chat',
  active_capability: 'mastery_path',
}

test('parses a valid decision while ignoring runtime audit metadata', () => {
  // Break caught: an added runtime audit key crashes a render-path parser.
  const parsed = parseLearningDecision(decision)
  assert.equal(parsed?.activity.kind, 'guided_attempt')
  assert.equal(parsed?.thread_id, 'thread-1')
})

test('rejects unknown scopes, missing activities, and impossible help levels', () => {
  // Break caught: malformed decision metadata is treated as a displayable learning activity.
  assert.equal(parseLearningDecision({ ...decision, scope: 'broad' }), null)
  assert.equal(parseLearningDecision({ ...decision, activity: undefined }), null)
  assert.equal(
    parseLearningDecision({ ...decision, activity: { ...decision.activity, help_level: 5 } }),
    null
  )
  assert.equal(parseLearningDecision({ nope: true }), null)
})

const proposal = {
  path_id: 'proposal-1',
  name: 'Signals',
  goal: 'Learn signals from examples',
  description: 'A practical route through basic signals.',
  sources: [
    {
      id: 'source-1',
      kind: 'file',
      source_id: 'signals.pdf',
      label: 'Signals notes',
      excerpt: '',
      position: 0,
      available: true,
      metadata: {},
    },
  ],
  modules: [
    {
      id: 'module-1',
      name: 'Foundations',
      order: 0,
      pass_threshold: 0.7,
      knowledge_points: [
        { id: 'objective-1', name: 'Frequency', type: 'concept', module_id: 'module-1' },
      ],
    },
  ],
}

test('parses a valid tool-result path proposal', () => {
  // Break caught: an editable path draft from the tool result is discarded before approval.
  const parsed = parseLearningPathProposal({ proposal })
  assert.equal(parsed?.modules[0]?.knowledge_points[0]?.name, 'Frequency')
})

test('rejects a proposal with an empty module without throwing', () => {
  // Break caught: an invalid draft enables an approval path or throws while rendering.
  assert.equal(
    parseLearningPathProposal({
      proposal: { ...proposal, modules: [{ ...proposal.modules[0], knowledge_points: [] }] },
    }),
    null
  )
})

test('selects the newest valid decision event', () => {
  // Break caught: a stale streamed decision wins over the final routing decision.
  const message = {
    role: 'assistant',
    content: '',
    events: [
      { metadata: { learning_decision: { ...decision, reason: 'Older decision.' } } },
      { metadata: { learning_decision: decision } },
    ],
  } as unknown as MessageItem
  assert.equal(selectLearningDecision(message)?.reason, decision.reason)
})

test('selects a tool-result proposal nested under tool metadata', () => {
  // Break caught: the real streamed tool-result envelope hides a valid editable draft.
  const message = {
    role: 'assistant',
    content: '',
    events: [{ metadata: { tool_metadata: { proposal } } }],
  } as unknown as MessageItem
  assert.equal(selectLearningPathProposal(message)?.path_id, 'proposal-1')
})
