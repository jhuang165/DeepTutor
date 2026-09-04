'use client'

import { useTranslation } from 'react-i18next'
import type { TFunction } from 'i18next'

import type { LearningQueueItem } from '../model'

export interface LearningHomeProps {
  items: LearningQueueItem[]
  loading: boolean
  error?: Error | null
  onContinue: (item: LearningQueueItem) => void
}

function LearningQueueSkeleton({ label }: { label: string }) {
  return (
    <div aria-label={label} className="mt-4 animate-pulse rounded-lg border p-4">
      <div className="h-4 w-2/3 rounded bg-muted" />
      <div className="mt-3 h-9 rounded bg-muted" />
    </div>
  )
}

function ContinueLearningCard({
  item,
  onContinue,
  title,
  continueLabel,
  reasonText,
}: {
  item: LearningQueueItem
  onContinue: (item: LearningQueueItem) => void
  title: string
  continueLabel: string
  reasonText: string
}) {
  const activityTitle =
    typeof item.activity?.objective === 'string' ? item.activity.objective : title
  return (
    <article className="mt-4 min-w-0 rounded-lg border p-4">
      <p className="break-words font-medium">{activityTitle}</p>
      <p className="mt-1 break-words text-sm text-muted-foreground">{reasonText}</p>
      <button
        type="button"
        className="mt-3 rounded bg-primary px-3 py-2 text-primary-foreground"
        onClick={() => onContinue(item)}
      >
        {continueLabel}
      </button>
    </article>
  )
}

function queueReasonText(item: LearningQueueItem, t: TFunction): string {
  const data = item.reason_data ?? {
    objective: '',
    goal: '',
    path_name: '',
    answer_state: '',
  }
  const objective = data.objective || t('this objective')
  switch (item.reason) {
    case 'unfinished_attempt':
      return data.answer_state === 'pending_grading'
        ? t('Your answer for {{objective}} is waiting for grading.', {
            objective,
          })
        : t('Answer the outstanding question for {{objective}}.', {
            objective,
          })
    case 'resume_lesson':
      return t('Resume the lesson: {{goal}}.', { goal: data.goal })
    case 'due_review':
      return t('Review {{objective}}; its spaced-repetition practice is due.', {
        objective,
      })
    case 'needs_transfer':
      return t('Apply {{goal}} to a new situation to show transfer.', { goal: data.goal })
    case 'continue_path':
      return t('Continue {{pathName}} with its next unmastered objective.', {
        pathName: data.path_name,
      })
  }
}

export function LearningHome({ items, loading, error, onContinue }: LearningHomeProps) {
  const { t } = useTranslation()
  const next = items[0]
  const dueReviews = items.filter(item => item.reason === 'due_review').length
  return (
    <section aria-labelledby="learning-home-title" className="min-w-0 max-w-full">
      <h1 id="learning-home-title" className="text-xl font-semibold">
        {t('What do you want to understand?')}
      </h1>
      {dueReviews > 0 ? (
        <p className="mt-2 text-sm">
          {dueReviews} {t(dueReviews === 1 ? 'review due' : 'reviews due')}
        </p>
      ) : null}
      {loading ? (
        <LearningQueueSkeleton label={t('Loading learning queue')} />
      ) : next ? (
        <ContinueLearningCard
          item={next}
          onContinue={onContinue}
          title={t('Your next activity')}
          continueLabel={t('Continue learning')}
          reasonText={queueReasonText(next, t)}
        />
      ) : null}
      {error ? (
        <p role="status" className="mt-3 text-sm text-muted-foreground">
          {t('Learning suggestions are unavailable.')}
        </p>
      ) : null}
    </section>
  )
}
