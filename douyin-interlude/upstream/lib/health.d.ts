/**
 * P3: In-memory rolling health metrics for the Console panel.
 * Counters are maintained per story and reported via a Console-exposed getter.
 * No persistence: resets on plugin reload, which is the expected behaviour for
 * a "since last reload" health view.
 */
export interface HealthSnapshot {
    narrativeTotal: number;
    narrativeFailed: number;
    structureMissing: number;
    recoverySaved: number;
    replyModes: {
        immediate: number;
        none: number;
        delayed: number;
        noDelivery: number;
    };
    sideTaskTotal: number;
    sideTaskFailed: number;
    proactiveTotal: number;
    proactiveSent: number;
    inputTokens: number;
    cachedTokens: number;
    latenciesMs: number[];
    sinceAt: string;
}
export declare class HealthMonitor {
    private stories;
    private state;
    recordNarrativeComplete(storyId: string, latencyMs: number, replyMode: string): void;
    recordNarrativeFailed(storyId: string): void;
    recordStructureMissing(storyId: string): void;
    recordRecoverySaved(storyId: string): void;
    recordSideTask(storyId: string, ok: boolean): void;
    recordProactive(storyId: string, sent: boolean): void;
    recordTokens(storyId: string, input: number, cached: number): void;
    snapshot(storyId: string): HealthSnapshot & {
        successRate: number;
        structureMissingRate: number;
        cacheHitRate: number;
        proactiveRate: number;
        medianLatencyMs: number;
    };
    all(): Record<string, ReturnType<HealthMonitor['snapshot']>>;
}
