export interface ForwardReadLimits {
    maxNodes: number;
    maxCharacters: number;
    maxDepth: number;
}
export interface ForwardReadResult {
    content: string;
    nodeCount: number;
    forwardCount: number;
    truncated: boolean;
    failed: boolean;
}
export declare function readForwardContent(session: any, limits?: Partial<ForwardReadLimits>): Promise<ForwardReadResult | undefined>;
export declare function forwardReadLimits(value?: Partial<ForwardReadLimits>): ForwardReadLimits;
export declare function extractForwardIds(content: unknown): string[];
export declare function normalizeForwardMessages(messages: unknown[], limits?: Partial<ForwardReadLimits>, depth?: number): ForwardReadResult;
