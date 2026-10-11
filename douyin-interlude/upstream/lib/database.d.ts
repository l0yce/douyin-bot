import { Context } from 'koishi';
import { InterludeArc, InterludeParticipant, InterludeScene, InterludeStory, NarrativeFact, NarrativeIntent, NarrativeMemory, OverlaySnapshot, SchedulePreplanRecord, ScriptEntry, SeededWorldEvent, StatePatchProposal, StickerAsset, WebObservation } from './types';
import type { QzonePostRecord } from './qzone';
import type { EndpointRow, StoryAliasRecord } from './endpoints';
import type { WorkRow } from './works';
declare module 'koishi' {
    interface Tables {
        interlude_story: InterludeStory;
        interlude_participant: InterludeParticipant;
        interlude_script_entry: ScriptEntry;
        interlude_memory: NarrativeMemory;
        interlude_intent: NarrativeIntent;
        interlude_scene: InterludeScene;
        interlude_arc: InterludeArc;
        interlude_fact: NarrativeFact;
        interlude_state_patch: StatePatchProposal;
        interlude_overlay_snapshot: OverlaySnapshot;
        interlude_sticker: StickerAsset;
        interlude_web_observation: WebObservation;
        interlude_schedule_preplan: SchedulePreplanRecord;
        interlude_seeded_event: SeededWorldEvent;
        interlude_qzone_post: QzonePostRecord;
        interlude_endpoint: EndpointRow;
        interlude_story_alias: StoryAliasRecord;
        interlude_work: WorkRow;
    }
}
export declare function registerTables(ctx: Context): void;
