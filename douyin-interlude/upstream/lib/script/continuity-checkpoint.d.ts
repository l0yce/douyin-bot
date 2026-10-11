import { CompactionDecision, ScriptEntry } from '../types';
/** A checkpoint consumes a contiguous prefix. Keeping the newest suffix here
 * would permanently skip the older evidence when lastEntryId advances. */
export declare function compactionPrefix(entries: ScriptEntry[], budget: number): ScriptEntry[];
export declare function assertContinuityReview(decision: CompactionDecision): void;
