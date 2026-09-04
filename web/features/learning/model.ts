import type { components } from '@/contracts/generated/api'
import type { MessageItem } from '@/features/chat/ChatStateAdapter'

export type LearningQueueItem = components['schemas']['LearningQueueItem']
export type LearningThread = components['schemas']['LearningThread']
export type LearningEvidence = components['schemas']['EvidenceRecord']

export interface LearningActivity {
  kind: string
  objective: string
  learner_action: string
  knowledge_type: string
  recipe_id: string
  recipe_version: number
  recipe_step: number
  help_level: 0 | 1 | 2 | 3 | 4
  source_refs: string[]
  assessment_method: string
  independent_required: boolean
  transfer_required: boolean
  next_action: string
}

export interface LearningDecision {
  scope: 'answer' | 'lesson' | 'path'
  route: string
  goal: string
  language: 'en' | 'zh'
  thread_id: string
  objective_id: string
  activity: LearningActivity
  reason: string
  confidence: number
  requires_approval: boolean
  source_policy: 'attached_only' | 'attached_preferred' | 'open'
}

export interface LearningSource {
  id: string
  kind: string
  source_id: string
  label: string
  excerpt: string
  position: number
  available: boolean
  metadata: Record<string, unknown>
}

export interface LearningObjective {
  id: string
  name: string
  type: string
  module_id: string
}

export interface LearningModule {
  id: string
  name: string
  order: number
  pass_threshold: number
  knowledge_points: LearningObjective[]
}

export interface LearningPathDraft {
  path_id: string
  name: string
  goal: string
  description: string
  starting_point: string
  teaching_preferences: string
  sources: LearningSource[]
  modules: LearningModule[]
}

const MAX = {
  route: 64,
  goal: 2000,
  reason: 500,
  objective: 2000,
  learnerAction: 4000,
} as const

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null
}

function string(value: unknown, maximum = Number.POSITIVE_INFINITY): string | null {
  return typeof value === 'string' && value.length <= maximum ? value : null
}

function finite(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function stringList(value: unknown): string[] | null {
  return Array.isArray(value) && value.every(item => typeof item === 'string') ? [...value] : null
}

function parseActivity(value: unknown): LearningActivity | null {
  const activity = record(value)
  if (!activity) return null
  const helpLevel = finite(activity.help_level)
  const recipeVersion = finite(activity.recipe_version)
  const recipeStep = finite(activity.recipe_step)
  const result: LearningActivity = {
    kind: string(activity.kind) ?? '',
    objective: string(activity.objective, MAX.objective) ?? '',
    learner_action: string(activity.learner_action, MAX.learnerAction) ?? '',
    knowledge_type: string(activity.knowledge_type) ?? '',
    recipe_id: string(activity.recipe_id) ?? '',
    recipe_version: recipeVersion ?? -1,
    recipe_step: recipeStep ?? -1,
    help_level: (helpLevel ?? -1) as LearningActivity['help_level'],
    source_refs: stringList(activity.source_refs) ?? [],
    assessment_method: string(activity.assessment_method) ?? '',
    independent_required: activity.independent_required === true,
    transfer_required: activity.transfer_required === true,
    next_action: string(activity.next_action) ?? '',
  }
  if (
    !result.kind ||
    !result.objective ||
    !result.learner_action ||
    !result.knowledge_type ||
    !result.recipe_id ||
    !result.assessment_method ||
    recipeVersion === null ||
    recipeVersion < 1 ||
    recipeStep === null ||
    recipeStep < 0 ||
    helpLevel === null ||
    !Number.isInteger(helpLevel) ||
    helpLevel < 0 ||
    helpLevel > 4 ||
    typeof activity.independent_required !== 'boolean' ||
    typeof activity.transfer_required !== 'boolean'
  )
    return null
  return result
}

/** Parses untrusted SESSION/DONE metadata without making render paths throw. */
export function parseLearningDecision(value: unknown): LearningDecision | null {
  const raw = record(value)
  if (!raw) return null
  const scope = raw.scope
  const language = raw.language
  const confidence = finite(raw.confidence)
  const activity = parseActivity(raw.activity)
  const decision: LearningDecision = {
    scope: scope as LearningDecision['scope'],
    route: string(raw.route, MAX.route) ?? '',
    goal: string(raw.goal, MAX.goal) ?? '',
    language: language as LearningDecision['language'],
    thread_id: string(raw.thread_id, 128) ?? '',
    objective_id: string(raw.objective_id) ?? '',
    activity: activity as LearningActivity,
    reason: string(raw.reason, MAX.reason) ?? '',
    confidence: confidence ?? -1,
    requires_approval: raw.requires_approval === true,
    source_policy: raw.source_policy as LearningDecision['source_policy'],
  }
  if (
    !['answer', 'lesson', 'path'].includes(decision.scope) ||
    !decision.route ||
    !decision.goal ||
    !['en', 'zh'].includes(decision.language) ||
    !activity ||
    !decision.reason ||
    confidence === null ||
    confidence < 0 ||
    confidence > 1 ||
    typeof raw.requires_approval !== 'boolean' ||
    !['attached_only', 'attached_preferred', 'open'].includes(decision.source_policy) ||
    decision.requires_approval !== (decision.scope === 'path')
  )
    return null
  return decision
}

function parseSource(value: unknown): LearningSource | null {
  const source = record(value)
  if (!source) return null
  const parsed: LearningSource = {
    id: string(source.id) ?? '',
    kind: string(source.kind) ?? '',
    source_id: string(source.source_id) ?? '',
    label: string(source.label, 200) ?? '',
    excerpt: string(source.excerpt, 8000) ?? '',
    position: finite(source.position) ?? -1,
    available: source.available === true,
    metadata: record(source.metadata) ?? {},
  }
  return parsed.id &&
    parsed.kind &&
    parsed.label &&
    Number.isInteger(parsed.position) &&
    typeof source.available === 'boolean' &&
    record(source.metadata)
    ? parsed
    : null
}

function parseModule(value: unknown): LearningModule | null {
  const rawModule = record(value)
  if (!rawModule || !Array.isArray(rawModule.knowledge_points)) return null
  const points = rawModule.knowledge_points.map(point => {
    const raw = record(point)
    if (!raw) return null
    const parsed: LearningObjective = {
      id: string(raw.id) ?? '',
      name: string(raw.name) ?? '',
      type: string(raw.type) ?? '',
      module_id: string(raw.module_id) ?? '',
    }
    return parsed.id && parsed.name && parsed.type && parsed.module_id ? parsed : null
  })
  const order = finite(rawModule.order)
  const passThreshold = finite(rawModule.pass_threshold)
  if (
    !points.every((point): point is LearningObjective => point !== null) ||
    points.length === 0 ||
    !string(rawModule.id) ||
    !string(rawModule.name) ||
    order === null ||
    !Number.isInteger(order) ||
    passThreshold === null
  )
    return null
  return {
    id: rawModule.id as string,
    name: rawModule.name as string,
    order,
    pass_threshold: passThreshold,
    knowledge_points: points,
  }
}

/** Parses tool-result proposal metadata; malformed drafts are simply omitted. */
export function parseLearningPathProposal(value: unknown): LearningPathDraft | null {
  const container = record(value)
  const toolMetadata = record(container?.tool_metadata)
  const raw = record(
    container?.proposal ?? container?.learning_path_draft ?? toolMetadata?.proposal ?? value
  )
  if (!raw || !Array.isArray(raw.modules) || !Array.isArray(raw.sources)) return null
  const modules = raw.modules.map(parseModule)
  const sources = raw.sources.map(parseSource)
  const pathId = string(raw.path_id)
  if (
    !pathId ||
    !modules.every((module): module is LearningModule => module !== null) ||
    !sources.every((source): source is LearningSource => source !== null) ||
    modules.length === 0
  )
    return null
  return {
    path_id: pathId,
    name: string(raw.name, 120) ?? '',
    goal: string(raw.goal, MAX.goal) ?? '',
    description: string(raw.description, 500) ?? '',
    starting_point: string(raw.starting_point, 2000) ?? '',
    teaching_preferences: string(raw.teaching_preferences, 2000) ?? '',
    sources,
    modules,
  }
}

function newestEventValue<T>(message: MessageItem, parse: (value: unknown) => T | null): T | null {
  for (const event of [...(message.events ?? [])].reverse()) {
    const metadata = event.metadata
    const parsed = parse(metadata.learning_decision ?? metadata.proposal ?? metadata)
    if (parsed) return parsed
  }
  return null
}

export function selectLearningDecision(message: MessageItem): LearningDecision | null {
  return newestEventValue(message, parseLearningDecision)
}

export function selectLearningPathProposal(message: MessageItem): LearningPathDraft | null {
  return newestEventValue(message, parseLearningPathProposal)
}
