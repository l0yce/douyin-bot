import type { ScriptCommitDraft, ScriptEventDraft } from './contract';
export type ScriptDeliveryStatus = 'pending' | 'delivered' | 'partial' | 'failed' | 'cancelled';
export type ScriptDeliverySegmentStatus = 'pending' | 'delivered' | 'failed' | 'cancelled';
export type ScriptDeliverySegmentKind = 'message' | 'local-media' | 'native-face' | 'message-reaction';
export interface ScriptDeliverySegment {
    index: number;
    kind: ScriptDeliverySegmentKind;
    content: string;
    status: ScriptDeliverySegmentStatus;
    attemptedAt?: string;
    completedAt?: string;
    reason?: string;
}
export interface ScriptDeliveryAction {
    commitId: string;
    eventId: string;
    eventKind: ScriptEventDraft['kind'];
    participantId?: string;
    status: ScriptDeliveryStatus;
    segments: ScriptDeliverySegment[];
    updatedAt: string;
}
export interface ScriptDeliveryReference {
    commitId: string;
    eventId: string;
    scriptEntryId: number;
    segmentIndex: number;
}
/** Materialize an outbox-shaped ledger inside the authoritative script row.
 * This is deliberately metadata-only for M6.1: no second sender and no schema
 * migration can change the existing delivery timing. */
export declare function createScriptDeliveryActions(commit: ScriptCommitDraft): ScriptDeliveryAction[];
export declare function updateScriptDeliveryActions(current: unknown, reference: ScriptDeliveryReference, status: ScriptDeliverySegmentStatus, at: Date, reason?: string): ScriptDeliveryAction[] | undefined;
export declare function deliveryReference(event: ScriptEventDraft | undefined, scriptEntryId: number | undefined, segmentIndex?: number): ScriptDeliveryReference | undefined;
export declare function platformActionReference(commit: ScriptCommitDraft | undefined, scriptEntryId: number | undefined, kind: Exclude<ScriptDeliverySegmentKind, 'message'>, content?: string): ScriptDeliveryReference;
export declare function aggregateDeliveryStatus(segments: ScriptDeliverySegment[]): ScriptDeliveryStatus;
