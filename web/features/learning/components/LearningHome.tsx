'use client'

import { useTranslation } from 'react-i18next'

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
}: {
  item: LearningQueueItem
  onContinue: (item: LearningQueueItem) => void
  title: string
  continueLabel: string
}) {
  const activityTitle =
    typeof item.activity?.objective === 'string' ? item.activity.objective : title
  return (
    <article className="mt-4 min-w-0 rounded-lg border p-4">
      <p className="break-words font-medium">{activityTitle}</p>
      <p className="mt-1 break-words text-sm text-muted-foreground">{item.reason_text}</p>
      <button
        className="mt-3 rounded bg-primary px-3 py-2 text-primary-foreground"
        onClick={() => onContinue(item)}
      >
        {continueLabel}
      </button>
    </article>
  )
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
