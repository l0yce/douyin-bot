import { AgencyConfig, AgencyWindowState, ProactiveContactDraft } from './types';
export declare const DEFAULT_AGENCY_CONFIG: AgencyConfig;
export interface AgencyCapacityResult {
    allowed: boolean;
    reason: string;
    nextOpportunityAt?: Date;
}
export declare function resolveAgencyConfig(value?: Partial<AgencyConfig>): AgencyConfig;
export declare function normalizeAgencyWindowState(value: unknown): AgencyWindowState | undefined;
export declare function normalizeAgencyWindowDraft(value: unknown, now: Date, config: AgencyConfig, validSourceEntryIds: ReadonlySet<number>, fallbackSourceEntryId?: number): AgencyWindowState | undefined;
export declare function activeAgencyWindow(value: unknown, now?: Date): AgencyWindowState;
export declare function normalizeProactiveContact(value: unknown, now: Date, config: AgencyConfig, permittedParticipantIds: ReadonlySet<string>, validSourceEntryIds: ReadonlySet<number>, fallbackSourceEntryId?: number): ProactiveContactDraft | undefined;
export declare function evaluateAgencyCapacity(window: AgencyWindowState | undefined, candidate: ProactiveContactDraft, now: Date, config: AgencyConfig, lastCharacterMessageAt?: string): AgencyCapacityResult;
export declare function proactiveCandidateFingerprint(candidate: ProactiveContactDraft): string;
export declare function proactiveRecheckAt(candidate: ProactiveContactDraft, capacity: AgencyCapacityResult, window: AgencyWindowState, now: Date): Date;
