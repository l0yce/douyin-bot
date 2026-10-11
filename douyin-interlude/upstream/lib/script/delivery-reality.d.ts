import type { ScriptEntry } from '../types';
/** Only call with entries already filtered for the current relationship.
 * This is an execution annotation of the same script, never replacement prose. */
export declare function deliveryReality(entries: ScriptEntry[], participantId?: string, shareParticipantDetails?: boolean, limit?: number): {
    sourceEntryId: number;
    eventId: any;
    segments: any;
}[];
