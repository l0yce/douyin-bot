import { ScriptCommitDraft } from './contract';
export interface ScriptCommitValidation {
    valid: boolean;
    errors: string[];
}
/** Structural validation only; literary style never becomes a rejection rule. */
export declare function validateScriptCommit(commit: ScriptCommitDraft, separator?: string): ScriptCommitValidation;
