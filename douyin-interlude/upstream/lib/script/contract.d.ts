import type { NarrativePhase } from '../types';
export type ScriptEventKind = 'narrative' | 'message-perceived' | 'outgoing-message' | 'group-message' | 'future-intent' | 'browser-intent' | 'follow-up-promise' | 'platform-action';
export type ScriptDeliveryMode = 'immediate' | 'delayed';
export interface ScriptActionBinding {
    /** Immediate actions bind to literal message spans in the authoritative
     * prose. Delayed actions have not happened inside the current window yet. */
    status: 'bound' | 'unbound' | 'future';
    spans?: Array<{
        bubbleIndex: number;
        start: number;
        end: number;
    }>;
}
export interface ScriptEventDraft {
    eventId: string;
    commitId: string;
    kind: ScriptEventKind;
    actor: 'protagonist' | 'system';
    occurredAt: string;
    causedByEventIds: string[];
    participantId?: string;
    content?: string;
    bubbles?: string[];
    deliveryMode?: ScriptDeliveryMode;
    scriptBinding?: ScriptActionBinding;
    metadata?: Record<string, unknown>;
}
export interface ScriptCommitDraft {
    commitId: string;
    storyId: string;
    participantId: string;
    phase: NarrativePhase;
    window: {
        from: string;
        to: string;
    };
    prose: string;
    events: ScriptEventDraft[];
    /** M4 append identity: the message and prose remain one scene delta. */
    sceneDelta: {
        frameId: string;
        burstId: string;
        startsAfterEventId?: string;
        proseAppend: string;
        eventIds: string[];
    };
    sourceFormat: 'beta10' | 'script-first-v1';
}
export interface ScriptMessageEventReference {
    commitId: string;
    eventId: string;
    /** Source script row containing the authoritative commit and delivery ledger. */
    scriptEntryId?: number;
    eventKind: 'outgoing-message' | 'group-message';
    causedByEventIds: string[];
    fullContent: string;
    bubbleIndex: number;
    bubbleCount: number;
}
export declare function isOutgoingScriptEvent(event: ScriptEventDraft): event is ScriptEventDraft & {
    kind: 'outgoing-message' | 'group-message';
    content: string;
    bubbles: string[];
    deliveryMode: ScriptDeliveryMode;
};
export declare function messageEventReference(event: ScriptEventDraft, bubbleIndex?: number, scriptEntryId?: number): ScriptMessageEventReference | undefined;
