import type { OutgoingMessageDraft } from './types'
import { messageEventReference, ScriptEventDraft, ScriptMessageEventReference } from './script/contract'

export function attachMessageEvent(message: OutgoingMessageDraft, event: ScriptEventDraft | undefined, scriptEntryId?: number): OutgoingMessageDraft {
  const scriptEvent = event ? messageEventReference(event, 0, scriptEntryId) : undefined
  return scriptEvent ? { ...message, scriptEvent } : message
}

export function prepareOutgoingDelivery(message: OutgoingMessageDraft, bubbles: string[]): OutgoingMessageDraft | undefined {
  const [first, ...later] = bubbles
  if (!first) return undefined
  const scriptEvent = message.scriptEvent
    ? { ...message.scriptEvent, bubbleIndex: 0, bubbleCount: bubbles.length, fullContent: message.scriptEvent.fullContent || message.content }
    : undefined
  return {
    ...message,
    content: first,
    ...(later.length ? { laterSegments: later } : {}),
    ...(scriptEvent ? { scriptEvent } : {}),
  }
}

export function scriptEventPayload(message: OutgoingMessageDraft, bubbleIndex = 0) {
  if (!message.scriptEvent) return {}
  return { scriptEvent: { ...message.scriptEvent, bubbleIndex } }
}

export function restoreMessageEvent(value: unknown, content: string): ScriptMessageEventReference | undefined {
  if (!isRecord(value) || !isRecord(value.scriptEvent)) return undefined
  const event = value.scriptEvent
  if (typeof event.commitId !== 'string' || typeof event.eventId !== 'string') return undefined
  if (event.eventKind !== 'outgoing-message' && event.eventKind !== 'group-message') return undefined
  const causedByEventIds = Array.isArray(event.causedByEventIds)
    ? event.causedByEventIds.filter((item): item is string => typeof item === 'string')
    : []
  return {
    commitId: event.commitId,
    eventId: event.eventId,
    ...(typeof event.scriptEntryId === 'number' && Number.isSafeInteger(event.scriptEntryId) ? { scriptEntryId: event.scriptEntryId } : {}),
    eventKind: event.eventKind,
    causedByEventIds,
    fullContent: typeof event.fullContent === 'string' ? event.fullContent : content,
    bubbleIndex: typeof event.bubbleIndex === 'number' && Number.isSafeInteger(event.bubbleIndex) ? event.bubbleIndex : 0,
    bubbleCount: typeof event.bubbleCount === 'number' && Number.isSafeInteger(event.bubbleCount) ? event.bubbleCount : 1,
  }
}

export function deliveryEntryMetadata(message: OutgoingMessageDraft, extra: Record<string, unknown> = {}) {
  return {
    visible: true,
    interaction: message.interaction ?? null,
    ...(message.scriptEvent
      ? { ...message.scriptEvent, bubbleIndex: message.scriptEvent.bubbleIndex ?? 0 }
      : {}),
    ...extra,
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === 'object' && !Array.isArray(value)
}
