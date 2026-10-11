import type { NarrativeDecision } from '../types';
export interface AuthoredAction {
    id: string;
    start: number;
    end: number;
    content: string;
}
/** Syntax only: unwrap authored speech, never infer an action from prose.
 * Duplicate ids are not executable references. The original words survive. */
export declare function readAuthoredActions(script: string): {
    prose: string;
    actions: AuthoredAction[];
};
/** Resolve the same authored words before any transport normalization. Legacy
 * content remains compatible; a broken explicit reference cannot send another
 * independently authored answer. Delayed drafts retain their existing path. */
export declare function reconcileTransportReferences(decision: NarrativeDecision, alreadySent?: boolean, separator?: string): NarrativeDecision;
export declare function completeLegacyBubbleBlock(prose: string, content: string, separator: string): string;
