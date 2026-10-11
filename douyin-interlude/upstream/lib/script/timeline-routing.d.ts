import type { NarrativePhase, SchedulePreplanWindow } from '../types';
/** Routing changes model work, never prose resolution. A known schedule
 * boundary keeps the temporal editor even during a short conversation. */
export declare function needsTimelineDirector(phase: NarrativePhase, from: Date, now: Date, timezone: string, schedule?: SchedulePreplanWindow | null): boolean;
