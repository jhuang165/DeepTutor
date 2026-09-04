import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { LearningActivityPanel } from '@/features/learning/components/LearningActivityPanel'
import { LearningHome } from '@/features/learning/components/LearningHome'
import { LearningPathProposal } from '@/features/learning/components/LearningPathProposal'
import type {
  LearningDecision,
  LearningPathDraft,
  LearningQueueItem,
} from '@/features/learning/model'

const queueItem: LearningQueueItem = {
  thread_id: 'thread-1',
  path_id: 'path-1',
  objective_id: 'objective-1',
  activity: { kind: 'retrieval', objective: 'Recall the definition' },
  reason: 'due_review',
  reason_text: 'A review is due.',
  priority: 1,
  due_at: 1,
}

const pathDraft: LearningPathDraft = {
  path_id: 'draft-1',
  name: 'Signals',
  goal: 'Understand signals',
  description: 'An editable route.',
  starting_point: 'I know basic algebra.',
  teaching_preferences: 'Use concrete examples.',
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

const learningDecision: LearningDecision = {
  scope: 'lesson',
  route: 'mastery_path',
  goal: 'Understand signals',
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
    help_level: 0,
    source_refs: [],
    assessment_method: 'rubric',
    independent_required: true,
    transfer_required: false,
    next_action: 'Answer the prompt',
  },
  reason: 'Prediction exposes the current model.',
  confidence: 0.8,
  requires_approval: false,
  source_policy: 'open',
}

describe('LearningHome', () => {
  it('leaves queue failures non-destructive without creating another chat composer', () => {
    // Break caught: a queue error creates a disconnected second composer instead of preserving Task 4 ownership.
    render(
      <LearningHome items={[]} loading={false} error={new Error('offline')} onContinue={vi.fn()} />
    )
    expect(screen.getByRole('heading', { name: 'What do you want to understand?' })).toBeVisible()
    expect(screen.getByRole('status')).toHaveTextContent('Learning suggestions are unavailable.')
    expect(screen.queryByRole('textbox', { name: 'Ask anything' })).not.toBeInTheDocument()
  })

  it('shows one continuation and the due-review count', async () => {
    // Break caught: a pending learning queue item is hidden instead of offering a continuation.
    const onContinue = vi.fn()
    const user = userEvent.setup()
    render(<LearningHome items={[queueItem]} loading={false} onContinue={onContinue} />)
    expect(screen.getByText('1 review due')).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Continue learning' }))
    expect(onContinue).toHaveBeenCalledWith(queueItem)
  })

  it('sends the complete edited draft once and routes only after approval resolves', async () => {
    // Break caught: edited draft fields are lost, duplicate approval starts, or navigation runs before success.
    let resolveApproval: (pathId: string) => void = () => undefined
    const approval = new Promise<string>(resolve => {
      resolveApproval = resolve
    })
    const approvePath = vi.fn(() => approval)
    const onApproved = vi.fn()
    const user = userEvent.setup()
    render(
      <LearningPathProposal
        threadId="thread-1"
        draft={pathDraft}
        approvePath={approvePath}
        onApproved={onApproved}
      />
    )
    await user.clear(screen.getByRole('textbox', { name: 'Starting point' }))
    await user.type(screen.getByRole('textbox', { name: 'Starting point' }), 'I know calculus.')
    await user.clear(screen.getByRole('textbox', { name: 'Teaching preferences' }))
    await user.type(screen.getByRole('textbox', { name: 'Teaching preferences' }), 'Use diagrams.')
    await user.click(screen.getByRole('button', { name: 'Approve path and begin' }))
    expect(approvePath).toHaveBeenCalledWith(
      'thread-1',
      expect.objectContaining({
        starting_point: 'I know calculus.',
        teaching_preferences: 'Use diagrams.',
      })
    )
    expect(onApproved).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Approve path and begin' }))
    expect(approvePath).toHaveBeenCalledTimes(1)
    resolveApproval('path-1')
    await waitFor(() => expect(onApproved).toHaveBeenCalledWith('/mastery/path-1'))
  })

  it('gates approval when an editable module has no objectives', () => {
    // Break caught: a broad path with an empty module can bypass authenticated approval validation.
    render(
      <LearningPathProposal
        threadId="thread-1"
        draft={{ ...pathDraft, modules: [{ ...pathDraft.modules[0], knowledge_points: [] }] }}
        approvePath={vi.fn(async () => 'path-1')}
        onApproved={vi.fn()}
      />
    )
    expect(screen.getByRole('button', { name: 'Approve path and begin' })).toBeDisabled()
  })

  it('marks direct explanations as non-independent evidence', async () => {
    // Break caught: direct answers are displayed as evidence-earning attempts.
    const user = userEvent.setup()
    render(
      <LearningActivityPanel
        decision={learningDecision}
        evidence={[]}
        onHelp={vi.fn()}
        onVisualEmphasis={vi.fn()}
        onRigor={vi.fn()}
        onPacing={vi.fn()}
      />
    )
    expect(screen.getByText('Why this next?').closest('details')).not.toHaveAttribute('open')
    await user.click(screen.getByRole('button', { name: 'Explain directly' }))
    expect(screen.getByRole('status')).toHaveTextContent(
      'This attempt will not count as independent evidence.'
    )
  })
})
