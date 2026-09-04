import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'

import { LearningActivityPanel } from '@/features/learning/components/LearningActivityPanel'
import { LearningHome } from '@/features/learning/components/LearningHome'
import { LearningPathProposal } from '@/features/learning/components/LearningPathProposal'
import type {
  LearningDecision,
  LearningPathDraft,
  LearningQueueItem,
} from '@/features/learning/model'
import zhApp from '@/locales/zh/app.json'

const queueItem: LearningQueueItem = {
  thread_id: 'thread-1',
  path_id: 'path-1',
  objective_id: 'objective-1',
  activity: { kind: 'retrieval', objective: 'Recall the definition' },
  reason: 'due_review',
  reason_data: {
    objective: 'objective-1',
    goal: '',
    path_name: '',
    answer_state: '',
  },
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
    expect(screen.getByText('Review {{objective}}; its spaced-repetition practice is due.')).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Continue learning' }))
    expect(onContinue).toHaveBeenCalledWith(queueItem)
  })

  it('renders the empty-objective fallback in Chinese', async () => {
    // Production break caught: locale-neutral empty interpolation data renders
    // an empty label unless the frontend chooses a localized fallback.
    const zhI18n = createInstance()
    await zhI18n.init({
      resources: { zh: { app: zhApp } },
      lng: 'zh',
      defaultNS: 'app',
      keySeparator: false,
      interpolation: { escapeValue: false },
    })
    const item = {
      ...queueItem,
      reason: 'unfinished_attempt' as const,
      objective_id: '',
      reason_data: {
        objective: '',
        goal: '',
        path_name: '',
        answer_state: 'pending_answer' as const,
      },
    }

    render(
      <I18nextProvider i18n={zhI18n}>
        <LearningHome items={[item]} loading={false} onContinue={vi.fn()} />
      </I18nextProvider>
    )

    expect(screen.getByText('请回答“当前学习目标”尚未完成的问题。')).toBeVisible()
    expect(screen.queryByText(/this objective/i)).not.toBeInTheDocument()
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
    await user.click(screen.getByText('Path details'))
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

  it('keeps path details collapsed while the editable goal and approval stay primary', async () => {
    // Break caught: starting point, sources, preferences, modules, and objectives crowd the primary approval view.
    const user = userEvent.setup()
    render(
      <LearningPathProposal
        threadId="thread-1"
        draft={pathDraft}
        approvePath={vi.fn(async () => 'path-1')}
        onApproved={vi.fn()}
      />
    )

    expect(screen.getByRole('textbox', { name: 'I want to be able to' })).toBeVisible()
    expect(screen.getByRole('button', { name: 'Approve path and begin' })).toBeVisible()
    const disclosure = screen.getByText('Path details').closest('details')
    expect(disclosure).not.toHaveAttribute('open')
    expect(screen.getByRole('textbox', { name: 'Starting point' })).not.toBeVisible()

    await user.click(screen.getByText('Path details'))
    expect(screen.getByRole('textbox', { name: 'Starting point' })).toBeVisible()
    expect(screen.getByRole('textbox', { name: 'Teaching preferences' })).toBeVisible()
    expect(screen.getByRole('textbox', { name: 'Module {{count}}' })).toBeVisible()
    expect(screen.getByRole('textbox', { name: 'Objectives' })).toBeVisible()
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

  it('rejects whitespace-only proposal fields and submits trimmed values', async () => {
    // Production break caught: whitespace-only objectives pass the approval
    // gate, and surrounding whitespace reaches the server as durable path data.
    const whitespaceObjective = {
      ...pathDraft,
      modules: [
        {
          ...pathDraft.modules[0],
          knowledge_points: [{ ...pathDraft.modules[0].knowledge_points[0], name: '   ' }],
        },
      ],
    }
    const { unmount } = render(
      <LearningPathProposal
        threadId="thread-1"
        draft={whitespaceObjective}
        approvePath={vi.fn(async () => 'path-1')}
        onApproved={vi.fn()}
      />
    )
    expect(screen.getByRole('button', { name: 'Approve path and begin' })).toBeDisabled()
    unmount()

    const approvePath = vi.fn<
      (threadId: string, draft: LearningPathDraft) => Promise<string>
    >(async () => 'path-1')
    const trimmedDraft: LearningPathDraft = {
      ...pathDraft,
      goal: '  Understand signals  ',
      sources: [
        {
          id: 'source-a',
          kind: 'note',
          source_id: 'note-a',
          label: '  Signal notes  ',
          excerpt: 'kept',
          position: 0,
          available: true,
          metadata: { provenance: 'kept' },
        },
      ],
      modules: [
        {
          ...pathDraft.modules[0],
          name: '  Foundation  ',
          knowledge_points: [
            { ...pathDraft.modules[0].knowledge_points[0], name: '  Frequency  ' },
          ],
        },
      ],
    }
    render(
      <LearningPathProposal
        threadId="thread-1"
        draft={trimmedDraft}
        approvePath={approvePath}
        onApproved={vi.fn()}
      />
    )
    await userEvent.click(screen.getByRole('button', { name: 'Approve path and begin' }))
    expect(approvePath).toHaveBeenCalledWith(
      'thread-1',
      expect.objectContaining({
        goal: 'Understand signals',
        sources: [expect.objectContaining({ id: 'source-a', label: 'Signal notes' })],
        modules: [
          expect.objectContaining({
            name: 'Foundation',
            knowledge_points: [expect.objectContaining({ name: 'Frequency' })],
          }),
        ],
      })
    )
  })

  it('edits sources by stable id across reorder and delete operations', async () => {
    // Production break caught: deleting or moving the first source transfers a
    // different source's stable id, excerpt, or provenance onto the visible label.
    const approvePath = vi.fn<
      (threadId: string, draft: LearningPathDraft) => Promise<string>
    >(async () => 'path-1')
    const sources = ['a', 'b', 'c'].map((id, position) => ({
      id: `source-${id}`,
      kind: 'note',
      source_id: `note-${id}`,
      label: `Source ${id.toUpperCase()}`,
      excerpt: `Excerpt ${id}`,
      position,
      available: true,
      metadata: { provenance: id },
    }))
    render(
      <LearningPathProposal
        threadId="thread-1"
        draft={{ ...pathDraft, sources }}
        approvePath={approvePath}
        onApproved={vi.fn()}
      />
    )
    const user = userEvent.setup()
    await user.click(screen.getByText('Path details'))
    await user.click(screen.getByRole('button', { name: 'Move source down 1' }))
    await user.click(screen.getByRole('button', { name: 'Remove source 1' }))
    await user.click(screen.getByRole('button', { name: 'Approve path and begin' }))

    const submitted = approvePath.mock.calls[0][1]
    expect(submitted.sources).toEqual([
      expect.objectContaining({
        id: 'source-a',
        excerpt: 'Excerpt a',
        metadata: { provenance: 'a' },
        position: 0,
      }),
      expect.objectContaining({
        id: 'source-c',
        excerpt: 'Excerpt c',
        metadata: { provenance: 'c' },
        position: 1,
      }),
    ])
  })

  it('renders a localized generic approval failure instead of transport detail', async () => {
    // Production break caught: backend or proxy error text is rendered verbatim
    // into the learner-facing proposal.
    render(
      <LearningPathProposal
        threadId="thread-1"
        draft={pathDraft}
        approvePath={vi.fn(async () => {
          throw new Error('private upstream transport detail')
        })}
        onApproved={vi.fn()}
      />
    )
    await userEvent.click(screen.getByRole('button', { name: 'Approve path and begin' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Path approval failed. Please try again.'
    )
    expect(screen.queryByText(/private upstream/)).not.toBeInTheDocument()
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

  it('waits for persisted direct help and prevents concurrent help requests', async () => {
    // Break caught: direct-help status appears before persistence, repeated clicks launch duplicate
    // updates, and a successfully consumed level-four answer can be requested again.
    let resolveHelp: () => void = () => undefined
    const onHelp = vi.fn(
      () =>
        new Promise<void>(resolve => {
          resolveHelp = resolve
        })
    )
    const user = userEvent.setup()
    render(
      <LearningActivityPanel
        decision={learningDecision}
        evidence={[]}
        onHelp={onHelp}
        onVisualEmphasis={vi.fn()}
        onRigor={vi.fn()}
        onPacing={vi.fn()}
      />
    )

    const direct = screen.getByRole('button', { name: 'Explain directly' })
    await user.click(direct)
    await user.click(direct)

    expect(onHelp).toHaveBeenCalledTimes(1)
    expect(direct).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Hint' })).toBeDisabled()
    expect(screen.queryByRole('status')).not.toBeInTheDocument()

    resolveHelp()
    await waitFor(() => expect(direct).toHaveAttribute('aria-busy', 'false'))
    expect(direct).toBeDisabled()
    expect(screen.getByRole('status')).toHaveTextContent(
      'This attempt will not count as independent evidence.'
    )
  })

  it('progresses hints through levels one, two, and three before direct answer four', async () => {
    // Production break caught: every Hint click requests absolute level one,
    // making the server reject the second click and levels two/three unreachable.
    const onHelp = vi.fn<
      (level: 0 | 1 | 2 | 3 | 4) => Promise<void>
    >(async () => undefined)
    render(
      <LearningActivityPanel
        decision={learningDecision}
        evidence={[]}
        onHelp={onHelp}
        onVisualEmphasis={vi.fn()}
        onRigor={vi.fn()}
        onPacing={vi.fn()}
      />
    )
    const user = userEvent.setup()
    const hint = screen.getByRole('button', { name: 'Hint' })
    await user.click(hint)
    await user.click(hint)
    await user.click(hint)
    expect(onHelp.mock.calls.map(([level]) => level)).toEqual([1, 2, 3])
    expect(hint).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Explain directly' }))
    expect(onHelp.mock.calls.map(([level]) => level)).toEqual([1, 2, 3, 4])
    expect(screen.getByRole('button', { name: 'Explain directly' })).toBeDisabled()
  })

  it('does not advance the hint level when persistence fails', async () => {
    // Production break caught: a failed level-one request advances local state,
    // so retry skips to level two even though the server never accepted one.
    const onHelp = vi
      .fn<(level: 0 | 1 | 2 | 3 | 4) => Promise<void>>()
      .mockRejectedValueOnce(new Error('offline'))
      .mockResolvedValueOnce(undefined)
    render(
      <LearningActivityPanel
        decision={learningDecision}
        evidence={[]}
        onHelp={onHelp}
        onVisualEmphasis={vi.fn()}
        onRigor={vi.fn()}
        onPacing={vi.fn()}
      />
    )
    const user = userEvent.setup()
    const hint = screen.getByRole('button', { name: 'Hint' })
    await user.click(hint)
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Help could not be requested. Please try again.'
    )
    await user.click(hint)
    expect(onHelp.mock.calls.map(([level]) => level)).toEqual([1, 1])
  })

  it('marks every standalone learning action as a non-submit button', () => {
    // Production break caught: embedding a learning panel in a form submits it
    // when any standalone action omits an explicit button type.
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
    for (const button of screen.getAllByRole('button')) {
      expect(button).toHaveAttribute('type', 'button')
    }
  })
})
