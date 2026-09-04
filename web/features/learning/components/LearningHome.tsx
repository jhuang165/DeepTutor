import type { LearningQueueItem } from '../model'

export interface LearningHomeProps {
  items: LearningQueueItem[]
  loading: boolean
  error?: Error | null
  onContinue: (item: LearningQueueItem) => void
}

function LearningQueueSkeleton() {
  return (
    <div aria-label="Loading learning queue" className="mt-4 animate-pulse rounded-lg border p-4">
      <div className="h-4 w-2/3 rounded bg-muted" />
      <div className="mt-3 h-9 rounded bg-muted" />
    </div>
  )
}

function ContinueLearningCard({
  item,
  onContinue,
}: {
  item: LearningQueueItem
  onContinue: (item: LearningQueueItem) => void
}) {
  const title =
    typeof item.activity?.objective === 'string' ? item.activity.objective : 'Your next activity'
  return (
    <article className="mt-4 min-w-0 rounded-lg border p-4">
      <p className="break-words font-medium">{title}</p>
      <p className="mt-1 break-words text-sm text-muted-foreground">{item.reason_text}</p>
      <button
        className="mt-3 rounded bg-primary px-3 py-2 text-primary-foreground"
        onClick={() => onContinue(item)}
      >
        Continue learning
      </button>
    </article>
  )
}

export function LearningHome({ items, loading, error, onContinue }: LearningHomeProps) {
  const next = items[0]
  const dueReviews = items.filter(item => item.reason === 'due_review').length
  return (
    <section aria-labelledby="learning-home-title" className="min-w-0 max-w-full">
      <h1 id="learning-home-title" className="text-xl font-semibold">
        What do you want to understand?
      </h1>
      {dueReviews > 0 ? (
        <p className="mt-2 text-sm">
          {dueReviews} {dueReviews === 1 ? 'review' : 'reviews'} due
        </p>
      ) : null}
      {loading ? (
        <LearningQueueSkeleton />
      ) : next ? (
        <ContinueLearningCard item={next} onContinue={onContinue} />
      ) : null}
      {error ? (
        <p role="status" className="mt-3 text-sm text-muted-foreground">
          Learning suggestions are unavailable. You can still ask anything.
        </p>
      ) : null}
      <label className="mt-5 block text-sm font-medium" htmlFor="learning-home-composer">
        Ask anything
      </label>
      <textarea
        id="learning-home-composer"
        aria-label="Ask anything"
        className="mt-1 min-h-24 w-full min-w-0 rounded border p-2"
      />
    </section>
  )
}
