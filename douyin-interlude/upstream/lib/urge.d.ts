/** Scheduling metadata only: never a contact decision or a memory source. */
export interface UrgeConfig {
    enabled?: boolean;
    frequency?: 'low' | 'medium' | 'high' | 'custom';
    proactiveWillingnessThreshold?: number;
    advanced?: {
        hotMin?: number;
        hotMax?: number;
        idleMin?: number;
        idleMax?: number;
        burstMin?: number;
        burstMax?: number;
        slowMin?: number;
        slowMax?: number;
        halfLifeMinutes?: number;
        burstThreshold?: number;
        jitter?: number;
        extremeChance?: number;
        burstTtlMinutes?: number;
        burstBudget?: number;
        burstContactMinMinutes?: number;
    };
}
export interface UrgeState {
    version: 1;
    mode?: string;
    buckets: number[];
    value?: number;
    pace?: 'normal' | 'slow';
    suggested?: number;
    sourceEntryId?: number;
    armed?: {
        participantId: string;
        entryId: number;
        at: number;
    };
    burst?: {
        participantId: string;
        started: number;
        used: number;
    };
    /** Once spent, only a real incoming event opens a new contact episode. */
    spent?: boolean;
    reason?: string;
}
export declare function resolveUrgeConfig(raw?: UrgeConfig): {
    enabled: boolean;
    frequency: string;
    willingness: number;
    hot: readonly [number, number];
    idle: readonly [number, number];
    burst: readonly [number, number];
    slow: readonly [number, number];
    halfLife: number;
    threshold: number;
    jitter: number;
    extremeChance: number;
    ttl: number;
    budget: number;
    contactMin: number;
};
export type ResolvedUrgeConfig = ReturnType<typeof resolveUrgeConfig>;
export declare function normalizeUrgeState(raw: unknown, now: number): UrgeState;
export declare function urgeUserEvent(state: UrgeState, now: number): UrgeState;
export declare function urgeDensity(state: UrgeState, now: number, c: ResolvedUrgeConfig): number;
export declare function commitUrge(state: UrgeState, raw: unknown, script: string, entryId: number, target: string | undefined, now: number, c: ResolvedUrgeConfig, random?: () => number): UrgeState;
export declare function acknowledgeUrge(state: UrgeState, participantId: string, entryId: number, now: number): UrgeState;
export declare function urgeBurstActive(state: UrgeState, now: number, c: ResolvedUrgeConfig, participantId?: string): boolean;
export declare function planUrge(state: UrgeState, now: number, c: ResolvedUrgeConfig, restMinutes?: number, unavailable?: boolean, random?: () => number): {
    state: {
        version: 1;
        mode?: string;
        buckets: number[];
        value?: number;
        pace?: "normal" | "slow";
        suggested?: number;
        sourceEntryId?: number;
        armed?: {
            participantId: string;
            entryId: number;
            at: number;
        };
        burst?: {
            participantId: string;
            started: number;
            used: number;
        };
        /** Once spent, only a real incoming event opens a new contact episode. */
        spent?: boolean;
        reason?: string;
    };
    nextAdvanceAt: string;
    minutes: number;
    reason: string;
};
export declare function urgeInstruction(enabled: boolean, phase: string): "" | "\nAfter writing the full script and its actions, optionally return urge:{value:0..1, pace:\"normal\"|\"slow\", suggestedDelayMinutes:number, basisQuote:\"exact sentence from this script\"}. Reflect the protagonist's present impulse and natural next opportunity to continue life; slow suits sleep or sustained focus. This is only a scheduling handoff, not speech, a future event, or a second contact decision. Continue using the existing authored actions and proactiveContact for any contact.";
