'use client'

import { useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import type { LearningDecision, LearningEvidence } from '../model'

export interface LearningActivityPanelProps {
  decision: LearningDecision
  evidence: LearningEvidence[]
  evidenceError?: Error | null
  onHelp: (level: 0 | 1 | 2 | 3 | 4) => Promise<void>
  onVisualEmphasis: (emphasis: 'more' | 'less') => void
  onRigor: (rigor: 'more' | 'less') => void
  onPacing: (pacing: 'slower' | 'faster') => void
}

function EvidenceList({
  records,
  emptyLabel,
}: {
  records: LearningEvidence[]
  emptyLabel: string
}) {
  return records.length ? (
    <ul>
      {records.map(record => (
        <li key={record.evidence_id}>
          {record.outcome}: {record.activity_kind}
        </li>
      ))}
    </ul>
  ) : (
    <p>{emptyLabel}</p>
  )
}

export function LearningActivityPanel({
  decision,
  evidence,
  evidenceError = null,
  onHelp,
  onVisualEmphasis,
  onRigor,
  onPacing,
}: LearningActivityPanelProps) {
  const { t } = useTranslation()
  const helpPendingRef = useRef(false)
  const [pendingHelpLevel, setPendingHelpLevel] = useState<0 | 1 | 2 | 3 | 4 | null>(null)
  const [directHelpConfirmed, setDirectHelpConfirmed] = useState(false)
  const [helpError, setHelpError] = useState(false)
  const requestHelp = async (level: 0 | 1 | 2 | 3 | 4) => {
    if (helpPendingRef.current) return
    helpPendingRef.current = true
    setPendingHelpLevel(level)
    setHelpError(false)
    try {
      await onHelp(level)
      if (level === 4) setDirectHelpConfirmed(true)
    } catch {
      setHelpError(true)
    } finally {
      helpPendingRef.current = false
      setPendingHelpLevel(null)
    }
  }
  return (
    <section aria-labelledby="learning-activity-title" className="min-w-0 max-w-full">
      <h2 id="learning-activity-title" className="text-lg font-semibold">
        {decision.activity.objective}
      </h2>
      <p className="mt-2 break-words">{decision.activity.learner_action}</p>
      <details className="mt-3">
        <summary>{t('Why this next?')}</summary>
        <p className="mt-2 break-words">{decision.reason}</p>
      </details>
      <details className="mt-3">
        <summary>{t('Learning evidence')}</summary>
        {evidenceError ? (
          <p role="alert">{t('Learning evidence is unavailable. Please try again.')}</p>
        ) : (
          <EvidenceList records={evidence} emptyLabel={t('No learning evidence yet.')} />
        )}
      </details>
      <details className="mt-3">
        <summary>{t('Sources')}</summary>
        {decision.activity.source_refs.length ? (
          <ul>
            {decision.activity.source_refs.map(source => (
              <li key={source} className="break-all">
                {source}
              </li>
            ))}
          </ul>
        ) : (
          <p>{t('No sources attached.')}</p>
        )}
      </details>
      <div className="mt-4 flex flex-wrap gap-2">
        <button
          disabled={pendingHelpLevel !== null}
          aria-busy={pendingHelpLevel === 1}
          onClick={() => void requestHelp(1)}
        >
          {t('Hint')}
        </button>
        <button
          disabled={pendingHelpLevel !== null}
          aria-busy={pendingHelpLevel === 4}
          onClick={() => void requestHelp(4)}
        >
          {t('Explain directly')}
        </button>
        <button onClick={() => onVisualEmphasis('more')}>{t('More visual')}</button>
        <button onClick={() => onRigor('more')}>{t('More rigorous')}</button>
        <button onClick={() => onPacing('slower')}>{t('Slow down')}</button>
      </div>
      {helpError ? (
        <p role="alert" className="mt-3 text-sm">
          {t('Help could not be requested. Please try again.')}
        </p>
      ) : null}
      {directHelpConfirmed ? (
        <p role="status" className="mt-3 text-sm">
          {t('This attempt will not count as independent evidence.')}
        </p>
      ) : null}
    </section>
  )
}
