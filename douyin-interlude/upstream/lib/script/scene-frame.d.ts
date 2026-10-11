import type { AgencyWindowState, DialogueBurstState, InterludeScene, SceneFrame, ScenePresenceState, ScriptEntry, StoryState, WorkingDetail } from '../types';
import type { ScriptCommitDraft } from './contract';
import { type LifeHandoff } from './life-handoff';
export interface SceneFrameProjectionInput {
    storyId: string;
    now: Date;
    scene: InterludeScene | null;
    state: StoryState;
    recentEntries?: ScriptEntry[];
    workingDetails?: WorkingDetail[];
    scenePresence?: ScenePresenceState[];
    agencyWindow?: AgencyWindowState | null;
}
export interface DialogueBurstSignal {
    /** Relationship/group/life branch. It is hashed before persistence. */
    scope?: string;
    /** Current external-event text. Only hashed topic keys are persisted. */
    topicText?: string;
    /** A committed structural transition, never an elapsed-time timeout. */
    boundary?: boolean;
}
/**
 * Deterministically projects already-established evidence into a read-only
 * scene scaffold. It never asks a model to summarize the scene and never uses
 * elapsed time to change frame or burst identity.
 */
export declare function projectSceneFrame(input: SceneFrameProjectionInput): SceneFrame;
export declare function resolveDialogueBurst(frame: SceneFrame, previous: DialogueBurstState | undefined, startedAt: Date, signal?: DialogueBurstSignal): DialogueBurstState;
/** Materialize only the newly committed append; prior prose remains immutable. */
export declare function advanceSceneFrame(frame: SceneFrame, burst: DialogueBurstState, commit: ScriptCommitDraft, sourceEntryId: number, now: Date, handoff?: LifeHandoff): {
    frame: SceneFrame;
    burst: DialogueBurstState;
};
export declare function sceneFrameProvenanceErrors(frame: SceneFrame): string[];
