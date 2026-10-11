/** Local navigation only: these spans never replace the immutable source. */
export interface RecallSpan {
    start: number;
    end: number;
    keys: Set<string>;
}
export declare function recallKeys(text: string): string[];
export declare function indexOriginal(content: string): RecallSpan[];
export declare function scoreOriginal(keys: string[], spans: RecallSpan[]): {
    score: number;
    index: number;
};
export declare function originalWindow(content: string, spans: RecallSpan[], index: number, budget: number, queryKeys?: string[]): {
    content: string;
    start: number;
    end: number;
};
/** Bounded query cues; plans navigate to evidence, never become that evidence. */
export declare function recallFocus(message: string | undefined, topics: string[], intentSummaries: string[]): string;
