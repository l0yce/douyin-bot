import type { NarrativeInteraction, ScriptEntryDraft, TimelinePlan } from './types';
import { type LifeHandoff } from './script/life-handoff';
import type { ScriptCommitDraft } from './script/contract';
export declare function scriptEntryDraftForCommit(commit: ScriptCommitDraft, interaction: NarrativeInteraction | null, timelinePlan?: TimelinePlan, lifeHandoff?: LifeHandoff): ScriptEntryDraft;
