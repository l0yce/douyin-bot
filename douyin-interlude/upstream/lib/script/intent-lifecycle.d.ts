import type { NarrativeIntent } from '../types';
/** These tasks have their own executor, never the live narrator's completion. */
export declare function liveNarrativeIntents(intents: NarrativeIntent[]): NarrativeIntent[];
export declare function consumedLiveIntentIds(intents: NarrativeIntent[]): number[];
