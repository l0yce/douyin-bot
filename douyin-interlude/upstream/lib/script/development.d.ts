import type { ScriptEntry, StatePatchDraft, StatePatchProposal } from '../types';
/** References into the supplied originals keep observation, interpretation
 * and reception together without writing another psychological summary. */
export declare function interactionEvidence(entries: ScriptEntry[]): {
    participantId: string;
    feedbackEntryId: number;
    priorCommunicationEntryId: number;
    interpretationEntryIds: number[];
    responseEntryIds: number[];
}[];
export declare function reviewedDevelopmentSupport(draft: StatePatchDraft, entries: ScriptEntry[], participantId: string): boolean;
/** A quiet turn still has a life context. Use visible original text only as a
 * relevance query, never as a fresh learning observation or evidence weight. */
export declare function developmentContextQuery(userMessage: string | undefined, dueSummaries: string[], visibleEntries: ScriptEntry[]): string;
/** Canon stays intact. New development proposals use a small vocabulary. */
export declare function developmentDimension(target: StatePatchDraft['target'], path: string): string;
/** One completed scene contributes once regardless of prose length or turns.
 * Legacy rows without scene provenance remain readable but cannot multiply
 * confidence by their timestamps. */
export declare function developmentScenes(entries: ScriptEntry[]): number;
/** Provisional learning remains auditable in storage, but does not become a
 * prompt-side expectation until it has crossed a scene boundary. Applied
 * tendencies already passed the host's stricter evidence gate. */
export declare function promptReadyDevelopment(candidate: StatePatchProposal, entries: ScriptEntry[]): boolean;
