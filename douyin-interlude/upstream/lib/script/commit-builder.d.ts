import type { NarrativeDecision, NarrativePhase } from '../types';
import { type AuthoredAction } from './authored-actions';
import { ScriptCommitDraft, ScriptDeliveryMode, ScriptEventDraft } from './contract';
export interface ScriptFirstDecisionInput {
    storyId: string;
    participantId?: string;
    phase: NarrativePhase;
    from: Date;
    now: Date;
    decision: NarrativeDecision;
    messageSeparator?: string;
    splitReplyMessages?: boolean;
    /** Group output is normalized by the service before it enters this adapter. */
    groupReplyContent?: string;
    frameId?: string;
    burstId?: string;
    startsAfterEventId?: string;
}
/**
 * The only beta10 → V2 bridge. It does not call a model or invent narrative
 * decisions; it gives the existing single response stable commit/event ids.
 */
export declare function decisionToScriptCommit(input: ScriptFirstDecisionInput): ScriptCommitDraft;
export declare function findPrivateOutgoingMessageEvent(commit: ScriptCommitDraft, participantId: string, mode?: ScriptDeliveryMode, content?: string, separator?: string): ScriptEventDraft;
export declare function findGroupScriptEvent(commit: ScriptCommitDraft): ScriptEventDraft;
/** Bind an immediate transport action to the unique literal message text in
 * the current script without interpreting prose or adding another model task.
 * Ambiguous/missing text stays diagnostic and never rewrites the script. */
export declare function bindImmediateMessageAction(prose: string, bubbles: string[], actions?: AuthoredAction[]): any;
export declare function unboundImmediateMessageEvents(commit: ScriptCommitDraft): ScriptEventDraft[];
