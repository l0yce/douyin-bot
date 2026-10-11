import { calendarDayKey, localClockMinutes, storyLocalTimeContext } from './time'
import {
  SchedulePreplanBlock, SchedulePreplanBlockKind, SchedulePreplanDay, SchedulePreplanException,
  SchedulePreplanProposal, SchedulePreplanRecord, SchedulePreplanRegime, SchedulePreplanWeekday,
  SchedulePreplanWindow, ScriptEntry,
} from './types'

export interface SchedulePreplanConfig {
  enabled: boolean
  horizonDays: number
  reviewAfterLocalHour: number
  anchorAutoAdvance: boolean
  variationLevel: 'stable' | 'contextual' | 'granular'
  candidateActivationProbability: number
  candidateRevealMinutes: number
}

export const DEFAULT_SCHEDULE_PREPLAN_CONFIG: SchedulePreplanConfig = {
  enabled: true,
  horizonDays: 14,
  reviewAfterLocalHour: 3,
  anchorAutoAdvance: true,
  variationLevel: 'stable',
  candidateActivationProbability: 0.25,
  candidateRevealMinutes: 120,
}

const WEEKDAYS: SchedulePreplanWeekday[] = [
  'sunday', 'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday',
]
const KINDS: SchedulePreplanBlockKind[] = ['fixed', 'routine', 'flexible', 'open']
const PRIORITY: Record<SchedulePreplanBlockKind, number> = { fixed: 4, routine: 3, flexible: 2, open: 1 }

export function resolveSchedulePreplanConfig(value?: Partial<SchedulePreplanConfig>): SchedulePreplanConfig {
  return {
    enabled: value?.enabled !== false,
    horizonDays: clampInt(value?.horizonDays, 3, 30, 14),
    reviewAfterLocalHour: clampInt(value?.reviewAfterLocalHour, 0, 23, 3),
    anchorAutoAdvance: value?.anchorAutoAdvance !== false,
    variationLevel: ['stable', 'contextual', 'granular'].includes(String(value?.variationLevel))
      ? value!.variationLevel as SchedulePreplanConfig['variationLevel'] : 'stable',
    candidateActivationProbability: clampNumber(value?.candidateActivationProbability, 0.05, 0.5, 0.25),
    candidateRevealMinutes: clampInt(value?.candidateRevealMinutes, 15, 360, 120),
  }
}

export function normalizeSchedulePreplanRecord(value: unknown): SchedulePreplanRecord | undefined {
  if (!isRecord(value) || typeof value.storyId !== 'string') return undefined
  const validFrom = dateKey(value.validFrom)
  const validThrough = dateKey(value.validThrough)
  if (!validFrom || !validThrough) return undefined
  return {
    storyId: value.storyId,
    revision: clampInt(value.revision, 0, 1_000_000, 0),
    timezone: text(value.timezone, 127) || 'UTC',
    validFrom,
    validThrough,
    lastReviewedLocalDate: dateKey(value.lastReviewedLocalDate) ?? '',
    lastEvidenceEntryId: clampInt(value.lastEvidenceEntryId, 0, Number.MAX_SAFE_INTEGER, 0),
    reviewReason: text(value.reviewReason, 500),
    regimes: normalizeRegimes(value.regimes),
    exceptions: normalizeExceptions(value.exceptions),
    materializedDays: normalizeDays(value.materializedDays),
    createdAt: validDate(value.createdAt) ?? new Date(0),
    updatedAt: validDate(value.updatedAt) ?? new Date(0),
  }
}

export function schedulePreplanReviewDue(record: SchedulePreplanRecord | undefined, now: Date, timezone: string, config: SchedulePreplanConfig) {
  if (!config.enabled) return false
  const today = calendarDayKey(now, timezone)
  if (!record) return true
  if (record.timezone !== timezone) return true
  return record.lastReviewedLocalDate !== today && localClockMinutes(now, timezone) >= config.reviewAfterLocalHour * 60
}

export function schedulePreplanNeedsModel(
  record: SchedulePreplanRecord | undefined,
  evidence: ScriptEntry[],
  today: string,
  timezone: string,
  config: SchedulePreplanConfig,
) {
  if (!record || record.timezone !== timezone) return true
  if (evidence.some(entry => entry.id > record.lastEvidenceEntryId)) return true
  // An explicit empty record means “reviewed, but no reliable recurring
  // schedule exists yet”. It must not cause a model request on every sweep.
  if (!record.regimes.length) return false
  const coverageTarget = addDate(today, Math.max(1, config.horizonDays - 3))
  if (!record.regimes.some(regime => regime.from <= coverageTarget && (!regime.to || regime.to >= coverageTarget))) return true
  return record.validThrough < coverageTarget
}

export function refreshSchedulePreplan(
  record: SchedulePreplanRecord,
  today: string,
  timezone: string,
  config: SchedulePreplanConfig,
  now: Date,
  reason = 'Daily review found no schedule-changing evidence.',
): SchedulePreplanRecord {
  const validThrough = addDate(today, config.horizonDays - 1)
  return {
    ...record,
    timezone,
    validFrom: today,
    validThrough,
    lastReviewedLocalDate: today,
    reviewReason: reason,
    materializedDays: materializeSchedulePreplan(record.regimes, record.exceptions, today, config.horizonDays),
    updatedAt: now,
  }
}

export function applySchedulePreplanProposal(
  current: SchedulePreplanRecord | undefined,
  proposalValue: unknown,
  evidence: ScriptEntry[],
  today: string,
  timezone: string,
  config: SchedulePreplanConfig,
  now: Date,
  variationLevel: SchedulePreplanConfig['variationLevel'] = 'stable',
): SchedulePreplanRecord | undefined {
  const proposal = normalizeProposal(proposalValue, new Set(evidence.map(entry => entry.id)), variationLevel)
  if (!proposal) return current ? refreshSchedulePreplan(current, today, timezone, config, now, 'Invalid proposal ignored; existing Schedule Preplan retained.') : undefined
  if (proposal.outcome === 'unchanged' && current) {
    return {
      ...refreshSchedulePreplan(current, today, timezone, config, now, proposal.reason),
      lastEvidenceEntryId: Math.max(current.lastEvidenceEntryId, ...evidence.map(entry => entry.id), 0),
    }
  }

  let regimes = current?.regimes ?? []
  let exceptions = current?.exceptions ?? []
  if (proposal.outcome === 'replace' || !current) {
    regimes = proposal.regimes ?? []
    exceptions = proposal.exceptions ?? []
  } else {
    regimes = mergeBy(regimes, proposal.regimes ?? [], item => item.id)
    exceptions = mergeBy(exceptions, proposal.exceptions ?? [], item => item.date)
  }
  if (!regimes.length) {
    // Empty is a valid initial result when the story has not established any
    // recurring rhythm. Persist that review so the system waits for new
    // evidence instead of repeatedly paying for the same empty conclusion.
    if (!current) {
      return {
        storyId: '', revision: 1, timezone, validFrom: today, validThrough: addDate(today, config.horizonDays - 1),
        lastReviewedLocalDate: today,
        lastEvidenceEntryId: Math.max(...evidence.map(entry => entry.id), 0),
        reviewReason: proposal.reason,
        regimes: [], exceptions: [], materializedDays: [], createdAt: now, updatedAt: now,
      }
    }
    return refreshSchedulePreplan(current, today, timezone, config, now, 'Empty proposal ignored; existing Schedule Preplan retained.')
  }
  const validThrough = addDate(today, config.horizonDays - 1)
  return {
    storyId: current?.storyId ?? '',
    revision: (current?.revision ?? 0) + 1,
    timezone,
    validFrom: today,
    validThrough,
    lastReviewedLocalDate: today,
    lastEvidenceEntryId: Math.max(current?.lastEvidenceEntryId ?? 0, ...evidence.map(entry => entry.id), 0),
    reviewReason: proposal.reason,
    regimes: regimes.slice(-6),
    exceptions: exceptions.filter(item => item.date >= addDate(today, -1)).slice(-30),
    materializedDays: materializeSchedulePreplan(regimes, exceptions, today, config.horizonDays),
    createdAt: current?.createdAt ?? now,
    updatedAt: now,
  }
}

export function materializeSchedulePreplan(regimes: SchedulePreplanRegime[], exceptions: SchedulePreplanException[], startDate: string, horizonDays: number): SchedulePreplanDay[] {
  const days: SchedulePreplanDay[] = []
  for (let offset = 0; offset < Math.max(1, horizonDays); offset++) {
    const date = addDate(startDate, offset)
    const matching = regimes
      .filter(regime => regime.from <= date && (!regime.to || regime.to >= date))
      .sort((left, right) => right.from.localeCompare(left.from))[0]
    const weekday = WEEKDAYS[new Date(`${date}T00:00:00.000Z`).getUTCDay()]
    let blocks = matching?.weekly[weekday]?.map(block => ({ ...block })) ?? []
    const exception = exceptions.find(item => item.date === date)
    if (exception?.mode === 'replace') blocks = exception.blocks?.map(block => ({ ...block })) ?? []
    else if (exception) {
      const removed = new Set(exception.removeBlockIds ?? [])
      blocks = [...blocks.filter(block => !removed.has(block.id)), ...(exception.blocks ?? []).map(block => ({ ...block }))]
    }
    days.push({ date, blocks: resolveOverlaps(blocks).slice(0, 12) })
  }
  return days
}

/** Project only the coming twelve hours; the rest of the stored horizon never enters the main prompt. */
export function schedulePreplanWindow(record: SchedulePreplanRecord | undefined, now: Date, timezone: string, hours = 12, config: Pick<SchedulePreplanConfig, 'candidateActivationProbability' | 'candidateRevealMinutes'> = DEFAULT_SCHEDULE_PREPLAN_CONFIG): SchedulePreplanWindow | null {
  if (!record || record.timezone !== timezone) return null
  const local = storyLocalTimeContext(now, timezone)
  const today = local.date
  const startMinute = local.hour * 60 + Number(local.time.slice(3, 5))
  const endMinute = startMinute + Math.max(1, hours) * 60
  const blocks: SchedulePreplanWindow['blocks'] = []
  for (const day of record.materializedDays) {
    const dayOffset = dateDifference(today, day.date)
    if (dayOffset < 0 || dayOffset > 1) continue
    for (const block of day.blocks) {
      const start = dayOffset * 1_440 + timeMinutes(block.start)
      let end = dayOffset * 1_440 + timeMinutes(block.end)
      if (end <= start) end += 1_440
      if (end <= startMinute || start >= endMinute) continue
      if (block.tentative) {
        if (!isTentativeBlockActive(record.storyId, day.date, block.id, config.candidateActivationProbability)) continue
        const minutesUntil = (start - startMinute)
        if (minutesUntil > config.candidateRevealMinutes) {
          blocks.push({ ...block, label: '可能的个人安排', location: undefined, date: day.date, tentative: true })
          continue
        }
      }
      blocks.push({ ...block, date: day.date })
    }
  }
  const toTotal = endMinute
  const toDate = addDate(today, Math.floor(toTotal / 1_440))
  const toClock = clock(toTotal % 1_440)
  return {
    name: 'Schedule Preplan', timezone,
    from: `${today} ${clock(startMinute)}`,
    to: `${toDate} ${toClock}`,
    plannedNotObserved: true,
    revision: record.revision,
    blocks: blocks.slice(0, 8),
  }
}

export function nextSchedulePreplanTransition(record: SchedulePreplanRecord | undefined, now: Date, timezone: string, maxHours = 12) {
  const window = schedulePreplanWindow(record, now, timezone, maxHours)
  if (!window) return undefined
  const local = storyLocalTimeContext(now, timezone)
  const current = local.hour * 60 + Number(local.time.slice(3, 5))
  const candidates: number[] = []
  for (const block of window.blocks.filter(item => item.kind === 'fixed')) {
    const offset = dateDifference(local.date, block.date) * 1_440
    const start = offset + timeMinutes(block.start)
    let end = offset + timeMinutes(block.end)
    if (end <= start) end += 1_440
    if (start > current) candidates.push(start)
    if (end > current) candidates.push(end)
  }
  const next = candidates.sort((left, right) => left - right)[0]
  return next == null ? undefined : new Date(now.getTime() + (next - current) * 60_000)
}

function normalizeProposal(value: unknown, validEvidenceIds: ReadonlySet<number>, variationLevel: SchedulePreplanConfig['variationLevel']): SchedulePreplanProposal | undefined {
  if (!isRecord(value) || !['unchanged', 'extend', 'patch', 'replace'].includes(String(value.outcome))) return undefined
  const outcome = value.outcome as SchedulePreplanProposal['outcome']
  const reason = text(value.reason, 500)
  if (!reason) return undefined
  const sourceEntryIds = ids(value.sourceEntryIds).filter(id => validEvidenceIds.has(id))
  const allowTentative = variationLevel === 'granular'
  const regimes = normalizeRegimes(value.regimes, validEvidenceIds, allowTentative)
  const exceptions = normalizeExceptions(value.exceptions, validEvidenceIds, allowTentative)
  if ((outcome === 'patch' || outcome === 'replace') && validEvidenceIds.size && !sourceEntryIds.length && !regimes.some(item => item.sourceEntryIds?.length) && !exceptions.some(item => item.sourceEntryIds?.length)) return undefined
  return { outcome, reason, confidence: finite(value.confidence), sourceEntryIds, regimes, exceptions }
}

function normalizeRegimes(value: unknown, validEvidenceIds?: ReadonlySet<number>, allowTentative = true) {
  if (!Array.isArray(value)) return []
  return value.map(item => normalizeRegime(item, validEvidenceIds, allowTentative)).filter((item): item is SchedulePreplanRegime => !!item).slice(0, 6)
}

function normalizeRegime(value: unknown, validEvidenceIds?: ReadonlySet<number>, allowTentative = true): SchedulePreplanRegime | undefined {
  if (!isRecord(value) || !isRecord(value.weekly)) return undefined
  const id = slug(value.id, 80)
  const label = text(value.label, 120)
  const from = dateKey(value.from)
  const to = dateKey(value.to)
  if (!id || !label || !from || to && to < from) return undefined
  const weekly: SchedulePreplanRegime['weekly'] = {}
  for (const weekday of WEEKDAYS) {
    const blocks = normalizeBlocks(value.weekly[weekday], validEvidenceIds, allowTentative)
    if (blocks.length) weekly[weekday] = blocks
  }
  return { id, label, from, ...(to ? { to } : {}), weekly, sourceEntryIds: evidenceIds(value.sourceEntryIds, validEvidenceIds) }
}

function normalizeExceptions(value: unknown, validEvidenceIds?: ReadonlySet<number>, allowTentative = true) {
  if (!Array.isArray(value)) return []
  return value.map(item => normalizeException(item, validEvidenceIds, allowTentative)).filter((item): item is SchedulePreplanException => !!item).slice(0, 30)
}

function normalizeException(value: unknown, validEvidenceIds?: ReadonlySet<number>, allowTentative = true): SchedulePreplanException | undefined {
  if (!isRecord(value)) return undefined
  const date = dateKey(value.date)
  const mode = value.mode === 'replace' ? 'replace' as const : value.mode === 'patch' ? 'patch' as const : undefined
  const reason = text(value.reason, 300)
  if (!date || !mode || !reason) return undefined
  return {
    date, mode, reason,
    removeBlockIds: Array.isArray(value.removeBlockIds) ? value.removeBlockIds.map(item => slug(item, 80)).filter(Boolean).slice(0, 20) : [],
    blocks: normalizeBlocks(value.blocks, validEvidenceIds, allowTentative),
    sourceEntryIds: evidenceIds(value.sourceEntryIds, validEvidenceIds),
  }
}

function normalizeDays(value: unknown): SchedulePreplanDay[] {
  if (!Array.isArray(value)) return []
  return value.flatMap(item => isRecord(item) && dateKey(item.date)
    ? [{ date: dateKey(item.date)!, blocks: normalizeBlocks(item.blocks) }]
    : []).slice(0, 31)
}

function normalizeBlocks(value: unknown, validEvidenceIds?: ReadonlySet<number>, allowTentative = true) {
  if (!Array.isArray(value)) return []
  return value.map(item => normalizeBlock(item, validEvidenceIds, allowTentative)).filter((item): item is SchedulePreplanBlock => !!item).slice(0, 20)
}

function normalizeBlock(value: unknown, validEvidenceIds?: ReadonlySet<number>, allowTentative = true): SchedulePreplanBlock | undefined {
  if (!isRecord(value)) return undefined
  const id = slug(value.id, 80)
  const start = timeKey(value.start)
  const end = timeKey(value.end)
  const label = text(value.label, 160)
  const kind = KINDS.includes(value.kind as SchedulePreplanBlockKind) ? value.kind as SchedulePreplanBlockKind : undefined
  if (!id || !start || !end || start === end || !label || !kind) return undefined
  const location = text(value.location, 120)
  const tentative = allowTentative && value.tentative === true && (kind === 'flexible' || kind === 'open')
  return { id, start, end, label, kind, ...(location ? { location } : {}), ...(tentative ? { tentative: true } : {}), sourceEntryIds: evidenceIds(value.sourceEntryIds, validEvidenceIds) }
}

function isTentativeBlockActive(storyId: string, date: string, blockId: string, probability: number) {
  const input = `${storyId}|${date}|${blockId}`
  let hash = 2166136261
  for (let index = 0; index < input.length; index++) hash = Math.imul(hash ^ input.charCodeAt(index), 16777619)
  return ((hash >>> 0) / 0x1_0000_0000) < probability
}

function resolveOverlaps(blocks: SchedulePreplanBlock[]) {
  const chosen: SchedulePreplanBlock[] = []
  for (const candidate of [...blocks].sort((left, right) => PRIORITY[right.kind] - PRIORITY[left.kind] || timeMinutes(left.start) - timeMinutes(right.start))) {
    const start = timeMinutes(candidate.start)
    let end = timeMinutes(candidate.end)
    if (end <= start) end += 1_440
    const overlaps = chosen.some(block => {
      const otherStart = timeMinutes(block.start)
      let otherEnd = timeMinutes(block.end)
      if (otherEnd <= otherStart) otherEnd += 1_440
      return start < otherEnd && end > otherStart
    })
    if (!overlaps && !chosen.some(block => block.id === candidate.id)) chosen.push(candidate)
  }
  return chosen.sort((left, right) => timeMinutes(left.start) - timeMinutes(right.start))
}

function evidenceIds(value: unknown, valid?: ReadonlySet<number>) {
  const normalized = ids(value)
  return valid ? normalized.filter(id => valid.has(id)) : normalized
}

function ids(value: unknown) {
  return Array.isArray(value) ? Array.from(new Set(value.map(Number).filter(id => Number.isSafeInteger(id) && id > 0))).slice(0, 30) : []
}

function mergeBy<T>(current: T[], changes: T[], key: (item: T) => string) {
  const merged = new Map(current.map(item => [key(item), item]))
  for (const item of changes) merged.set(key(item), item)
  return [...merged.values()]
}

function dateKey(value: unknown) {
  const raw = typeof value === 'string' ? value.trim() : ''
  if (!/^\d{4}-\d{2}-\d{2}$/.test(raw)) return undefined
  const date = new Date(`${raw}T00:00:00.000Z`)
  return !Number.isNaN(date.getTime()) && date.toISOString().slice(0, 10) === raw ? raw : undefined
}

function addDate(value: string, days: number) {
  const date = new Date(`${value}T00:00:00.000Z`)
  date.setUTCDate(date.getUTCDate() + days)
  return date.toISOString().slice(0, 10)
}

function dateDifference(left: string, right: string) {
  return Math.round((new Date(`${right}T00:00:00.000Z`).getTime() - new Date(`${left}T00:00:00.000Z`).getTime()) / 86_400_000)
}

function timeKey(value: unknown) {
  const raw = typeof value === 'string' ? value.trim() : ''
  const match = /^(\d{2}):(\d{2})$/.exec(raw)
  if (!match || Number(match[1]) > 23 || Number(match[2]) > 59) return undefined
  return raw
}

function timeMinutes(value: string) {
  const [hour, minute] = value.split(':').map(Number)
  return hour * 60 + minute
}

function clock(minutes: number) {
  return `${String(Math.floor(minutes / 60)).padStart(2, '0')}:${String(minutes % 60).padStart(2, '0')}`
}

function slug(value: unknown, limit: number) {
  return typeof value === 'string' ? value.trim().replace(/[^\p{L}\p{N}_-]/gu, '-').replace(/-+/g, '-').replace(/^-|-$/g, '').slice(0, limit) : ''
}

function text(value: unknown, limit: number) {
  return typeof value === 'string' ? value.trim().replace(/[\r\n]+/g, ' ').slice(0, limit) : ''
}

function finite(value: unknown) {
  return typeof value === 'number' && Number.isFinite(value) ? Math.max(0, Math.min(1, value)) : undefined
}

function validDate(value: unknown) {
  const date = value instanceof Date ? value : typeof value === 'string' || typeof value === 'number' ? new Date(value) : undefined
  return date && !Number.isNaN(date.getTime()) ? date : undefined
}

function clampInt(value: unknown, min: number, max: number, fallback: number) {
  const number = Number(value)
  return Number.isFinite(number) ? Math.max(min, Math.min(max, Math.floor(number))) : fallback
}

function clampNumber(value: unknown, min: number, max: number, fallback: number) {
  const number = Number(value)
  return Number.isFinite(number) ? Math.max(min, Math.min(max, number)) : fallback
}

function isRecord(value: unknown): value is Record<string, any> {
  return !!value && typeof value === 'object' && !Array.isArray(value)
}
