'use client'

import { useState } from 'react'

import type { LearningDecision, LearningEvidence } from '../model'

export interface LearningActivityPanelProps {
  decision: LearningDecision
  evidence: LearningEvidence[]
  onHelp: (level: 0 | 1 | 2 | 3 | 4) => void
  onVisualEmphasis: (emphasis: 'more' | 'less') => void
  onRigor: (rigor: 'more' | 'less') => void
  onPacing: (pacing: 'slower' | 'faster') => void
}

function EvidenceList({ records }: { records: LearningEvidence[] }) {
  return records.length ? (
    <ul>
      {records.map(record => (
        <li key={record.evidence_id}>
          {record.outcome}: {record.activity_kind}
        </li>
      ))}
    </ul>
  ) : (
    <p>No learning evidence yet.</p>
  )
}

export function LearningActivityPanel({
  decision,
  evidence,
  onHelp,
  onVisualEmphasis,
  onRigor,
  onPacing,
}: LearningActivityPanelProps) {
  const [pendingHelpLevel, setPendingHelpLevel] = useState<number | null>(null)
  const requestHelp = (level: 0 | 1 | 2 | 3 | 4) => {
    setPendingHelpLevel(level)
    onHelp(level)
  }
  return (
    <section aria-labelledby="learning-activity-title" className="min-w-0 max-w-full">
      <h2 id="learning-activity-title" className="text-lg font-semibold">
        {decision.activity.objective}
      </h2>
      <p className="mt-2 break-words">{decision.activity.learner_action}</p>
      <details className="mt-3">
        <summary>Why this next?</summary>
        <p className="mt-2 break-words">{decision.reason}</p>
      </details>
      <details className="mt-3">
        <summary>Learning evidence</summary>
        <EvidenceList records={evidence} />
      </details>
      <details className="mt-3">
        <summary>Sources</summary>
        {decision.activity.source_refs.length ? (
          <ul>
            {decision.activity.source_refs.map(source => (
              <li key={source} className="break-all">
                {source}
              </li>
            ))}
          </ul>
        ) : (
          <p>No sources attached.</p>
        )}
      </details>
      <div className="mt-4 flex flex-wrap gap-2">
        <button onClick={() => requestHelp(1)}>Hint</button>
        <button onClick={() => requestHelp(4)}>Explain directly</button>
        <button onClick={() => onVisualEmphasis('more')}>More visual</button>
        <button onClick={() => onRigor('more')}>More rigorous</button>
        <button onClick={() => onPacing('slower')}>Slow down</button>
      </div>
      {pendingHelpLevel === 4 ? (
        <p role="status" className="mt-3 text-sm">
          This attempt will not count as independent evidence.
        </p>
      ) : null}
    </section>
  )
}
