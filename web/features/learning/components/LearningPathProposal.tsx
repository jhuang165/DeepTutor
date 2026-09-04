'use client'

import { useRef, useState } from 'react'
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

function normalizedDraft(draft: LearningPathDraft): LearningPathDraft {
  return {
    ...copyDraft(draft),
    name: draft.name.trim(),
    goal: draft.goal.trim(),
    sources: draft.sources
      .map(source => ({ ...source, label: source.label.trim() }))
      .filter(source => source.label.length > 0)
      .map((source, position) => ({ ...source, position })),
    modules: draft.modules.map(module => ({
      ...module,
      name: module.name.trim(),
      knowledge_points: module.knowledge_points
        .map(point => ({ ...point, name: point.name.trim() }))
        .filter(point => point.name.length > 0),
    })),
  }
}

function sourcesAtCurrentPositions(sources: LearningPathDraft['sources']) {
  return sources.map((source, position) => ({ ...source, position }))
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
  const [approvalFailed, setApprovalFailed] = useState(false)
  const sourceCounter = useRef(0)
  const submissionDraft = normalizedDraft(draft)
  const valid =
    Boolean(submissionDraft.name && submissionDraft.goal) &&
    submissionDraft.modules.length > 0 &&
    submissionDraft.modules.every(
      module => Boolean(module.name) && module.knowledge_points.length > 0
    )

  const updateModule = (index: number, update: Partial<LearningModule>) => {
    setDraft(current => ({
      ...current,
      modules: current.modules.map((module, moduleIndex) =>
        moduleIndex === index ? { ...module, ...update } : module
      ),
    }))
  }

  const updateSourceLabel = (sourceId: string, label: string) => {
    setDraft(current => ({
      ...current,
      sources: current.sources.map(source =>
        source.id === sourceId ? { ...source, label } : source
      ),
    }))
  }

  const moveSource = (sourceId: string, offset: -1 | 1) => {
    setDraft(current => {
      const sources = [...current.sources]
      const index = sources.findIndex(source => source.id === sourceId)
      const nextIndex = index + offset
      if (index < 0 || nextIndex < 0 || nextIndex >= sources.length) return current
      const [source] = sources.splice(index, 1)
      sources.splice(nextIndex, 0, source)
      return { ...current, sources: sourcesAtCurrentPositions(sources) }
    })
  }

  const removeSource = (sourceId: string) => {
    setDraft(current => ({
      ...current,
      sources: sourcesAtCurrentPositions(
        current.sources.filter(source => source.id !== sourceId)
      ),
    }))
  }

  const addSource = () => {
    setDraft(current => {
      const existing = new Set(current.sources.map(source => source.id))
      let id = ''
      do {
        sourceCounter.current += 1
        id = `new-source-${sourceCounter.current}`
      } while (existing.has(id))
      return {
        ...current,
        sources: [
          ...current.sources,
          {
            id,
            kind: 'note',
            source_id: '',
            label: '',
            excerpt: '',
            position: current.sources.length,
            available: true,
            metadata: {},
          },
        ],
      }
    })
  }

  const approve = async () => {
    if (!valid || approving) return
    setApproving(true)
    setApprovalFailed(false)
    try {
      const pathId = await approvePath(threadId, submissionDraft)
      onApproved(`/mastery/${pathId}`)
    } catch {
      setApprovalFailed(true)
    } finally {
      setApproving(false)
    }
  }

  return (
    <section aria-labelledby="learning-path-title" className="min-w-0 max-w-full">
      <h2 id="learning-path-title" className="text-lg font-semibold">
        {t('Proposed learning path')}
      </h2>
      <label className="mt-3 block text-sm" htmlFor="learning-goal">
        {t('I want to be able to')}
      </label>
      <textarea
        id="learning-goal"
        value={draft.goal}
        onChange={event => setDraft({ ...draft, goal: event.target.value })}
        className="mt-1 w-full min-w-0 rounded border p-2"
      />
      <details className="mt-4 min-w-0">
        <summary className="cursor-pointer text-sm font-medium">{t('Path details')}</summary>
        <div className="mt-2 min-w-0">
          <label className="mt-3 block text-sm" htmlFor="learning-starting-point">
            {t('Starting point')}
          </label>
          <textarea
            id="learning-starting-point"
            value={draft.starting_point}
            onChange={event => setDraft({ ...draft, starting_point: event.target.value })}
            className="mt-1 w-full min-w-0 rounded border p-2"
          />
          <fieldset className="mt-3 min-w-0">
            <legend className="block text-sm">{t('Sources')}</legend>
            <div className="mt-1 space-y-2">
              {draft.sources.map((source, index) => (
                <div key={source.id} className="min-w-0 rounded border p-2">
                  <label className="block text-sm" htmlFor={`learning-source-${index}`}>
                    {t('Source')} {index + 1}
                  </label>
                  <input
                    id={`learning-source-${index}`}
                    value={source.label}
                    onChange={event => updateSourceLabel(source.id, event.target.value)}
                    className="mt-1 w-full min-w-0 rounded border p-2"
                  />
                  <div className="mt-2 flex flex-wrap gap-2">
                    <button
                      type="button"
                      aria-label={`${t('Move source up')} ${index + 1}`}
                      disabled={index === 0}
                      onClick={() => moveSource(source.id, -1)}
                    >
                      {t('Move up')}
                    </button>
                    <button
                      type="button"
                      aria-label={`${t('Move source down')} ${index + 1}`}
                      disabled={index === draft.sources.length - 1}
                      onClick={() => moveSource(source.id, 1)}
                    >
                      {t('Move down')}
                    </button>
                    <button
                      type="button"
                      aria-label={`${t('Remove source')} ${index + 1}`}
                      onClick={() => removeSource(source.id)}
                    >
                      {t('Remove source')}
                    </button>
                  </div>
                </div>
              ))}
            </div>
            <button type="button" className="mt-2" onClick={addSource}>
              {t('Add source')}
            </button>
          </fieldset>
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
        </div>
      </details>
      {approvalFailed ? (
        <p role="alert" className="mt-3 text-sm">
          {t('Path approval failed. Please try again.')}
        </p>
      ) : null}
      <button
        type="button"
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
