import { type RecallSpan } from './recall-navigation';
/** Rebuildable navigation over already visibility-filtered original entries.
 * A frame identifies a scene; neither time gaps nor message counts split it.
 * Old entries without a frame remain singleton anchors. */
export interface EpisodeSource {
    content: string;
    occurredAt: string;
    participantId: string;
    kind: string;
    frameId?: string;
    tags?: string[];
    spans?: RecallSpan[];
    checkpoint?: {
        sceneId: number;
        firstEntryId: number;
        lastEntryId: number;
    };
}
export declare function buildEpisodeIndex(rows: Array<[number, EpisodeSource]>): Map<number, number[]>;
/** Keep the actual hit, then the closest original entries. Source ids always
 * describe text included in the result, not an omitted part of the episode. */
export declare function episodeExcerpt(rows: Array<[number, EpisodeSource]>, anchorId: number, budget?: number, queryKeys?: string[]): {
    sourceEntryIds: number[];
    content: string;
};
/** Tags are literal navigation spans, never generated summaries or facts. */
export declare function groundedEpisodeTags(content: string, draft: Record<string, unknown>): Record<string, string[]>;
export declare function episodeTagScore(query: string, tags?: string[]): 0 | 0.8;
