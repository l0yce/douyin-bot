import type { ScriptEntry } from '../types';
/** A bookmark into the visible originals, not another summary or scene author.
 * Communication references record occurrences, never infer an unanswered debt. */
export declare function continuationBookmark(entries: ScriptEntry[], from: Date, now: Date): {
    newEventEntryIds: number[];
    recentCommunications: {
        entryId: number;
        kind: string;
        participantId: string;
        occurredAt: string;
    }[];
    originalEndpoint?: {
        entryId: number;
        characterOffset: number;
    };
    writingStart: string;
    lastScript?: {
        entryId: number;
        kind: string;
        participantId: string;
        occurredAt: string;
    };
    establishedThrough: string;
};
/** Diagnostic only: long literal reuse is evidence to inspect, not a literary
 * rejection rule. Identical short questions must never be treated as a fault. */
export declare function proseReuseObservation(previous: string, next: string, width?: number): number;
