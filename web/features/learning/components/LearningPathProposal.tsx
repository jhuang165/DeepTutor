'use client'

import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import type { LearningModule, LearningPathDraft } from '../model'

export interface LearningPathProposalProps {
  threadId: string
  draft: LearningPathDraft
  approvePath: (threadId: string, draft: LearningPathDraft) => Promise<string>
  onApproved: (href: string) => void
}

function copyDraft(draft: LearningPathDraft): LearningPathDraft {
  return {
    ...draft,
    sources: draft.sources.map(source => ({ ...source, metadata: { ...source.metadata } })),
    modules: draft.modules.map(module => ({
      ...module,
      knowledge_points: module.knowledge_points.map(point => ({ ...point })),
    })),
  }
}

export function LearningPathProposal({
  threadId,
  draft: initialDraft,
  approvePath,
  onApproved,
}: LearningPathProposalProps) {
  const { t } = useTranslation()
  const [draft, setDraft] = useState(() => copyDraft(initialDraft))
  const [approving, setApproving] = useState(false)
  const [error, setError] = useState<Error | null>(null)
  const valid =
    draft.modules.length > 0 && draft.modules.every(module => module.knowledge_points.length > 0)

  const updateModule = (index: number, update: Partial<LearningModule>) => {
    setDraft(current => ({
      ...current,
      modules: current.modules.map((module, moduleIndex) =>
        moduleIndex === index ? { ...module, ...update } : module
      ),
    }))
  }

  const approve = async () => {
    if (!valid || approving) return
    setApproving(true)
    setError(null)
    try {
      const pathId = await approvePath(threadId, draft)
      onApproved(`/mastery/${pathId}`)
    } catch (reason) {
      setError(reason instanceof Error ? reason : new Error(String(reason)))
    } finally {
      setApproving(false)
    }
  }

  return (
    <section aria-labelledby="learning-path-title" className="min-w-0 max-w-full">
      <h2 id="learning-path-title" className="text-lg font-semibold">
        {t('Learning path proposal')}
      </h2>
      <label className="mt-3 block text-sm" htmlFor="learning-goal">
        {t('Goal')}
      </label>
      <textarea
        id="learning-goal"
        value={draft.goal}
        onChange={event => setDraft({ ...draft, goal: event.target.value })}
        className="mt-1 w-full min-w-0 rounded border p-2"
      />
      <label className="mt-3 block text-sm" htmlFor="learning-starting-point">
        {t('Starting point')}
      </label>
      <textarea
        id="learning-starting-point"
        value={draft.starting_point}
        onChange={event => setDraft({ ...draft, starting_point: event.target.value })}
        className="mt-1 w-full min-w-0 rounded border p-2"
      />
      <label className="mt-3 block text-sm" htmlFor="learning-sources">
        {t('Sources')}
      </label>
      <textarea
        id="learning-sources"
        value={draft.sources.map(source => source.label).join('\n')}
        onChange={event =>
          setDraft({
            ...draft,
            sources: event.target.value
              .split('\n')
              .filter(Boolean)
              .map((label, index) => ({
                ...(draft.sources[index] ?? {
                  id: `source-${index}`,
                  kind: 'note',
                  source_id: '',
                  excerpt: '',
                  position: index,
                  available: true,
                  metadata: {},
                }),
                label,
                position: index,
              })),
          })
        }
        className="mt-1 w-full min-w-0 rounded border p-2"
      />
      <label className="mt-3 block text-sm" htmlFor="learning-preferences">
        {t('Teaching preferences')}
      </label>
      <textarea
        id="learning-preferences"
        value={draft.teaching_preferences}
        onChange={event => setDraft({ ...draft, teaching_preferences: event.target.value })}
        className="mt-1 w-full min-w-0 rounded border p-2"
      />
      <ol className="mt-4 space-y-3">
        {draft.modules.map((module, index) => (
          <li key={module.id} className="min-w-0 rounded border p-3">
            <label className="block text-sm" htmlFor={`module-${module.id}`}>
              {t('Module {{count}}', { count: index + 1 })}
            </label>
            <input
              id={`module-${module.id}`}
              value={module.name}
              onChange={event => updateModule(index, { name: event.target.value })}
              className="mt-1 w-full min-w-0 rounded border p-2"
            />
            <label className="mt-2 block text-sm" htmlFor={`objectives-${module.id}`}>
              {t('Objectives')}
            </label>
            <textarea
              id={`objectives-${module.id}`}
              value={module.knowledge_points.map(point => point.name).join('\n')}
              onChange={event =>
                updateModule(index, {
                  knowledge_points: event.target.value
                    .split('\n')
                    .filter(Boolean)
                    .map((name, pointIndex) => ({
                      ...(module.knowledge_points[pointIndex] ?? {
                        id: `${module.id}-objective-${pointIndex}`,
                        type: 'concept',
                        module_id: module.id,
                      }),
                      name,
                    })),
                })
              }
              className="mt-1 w-full min-w-0 rounded border p-2"
            />
          </li>
        ))}
      </ol>
      {error ? (
        <p role="alert" className="mt-3 text-sm">
          {error.message}
        </p>
      ) : null}
      <button
        disabled={!valid || approving}
        aria-busy={approving}
        className="mt-4 rounded bg-primary px-3 py-2 text-primary-foreground disabled:opacity-50"
        onClick={() => void approve()}
      >
        {t('Approve path and begin')}
      </button>
    </section>
  )
}
