import { NarrativePhase } from './types';
export type InterludeLogLevel = 'error' | 'warn' | 'info' | 'debug';
export type InterludeLogColorTheme = 'dark' | 'light';
export type InterludeLogAction = 'receive' | 'send' | 'processing' | 'complete' | 'trigger' | 'emotion' | 'memory' | 'advance' | 'agency' | 'group' | 'error' | 'retry' | 'warning' | 'waiting' | 'system';
export interface LayeredLogInput {
    level: InterludeLogLevel;
    phase?: NarrativePhase;
    protagonist?: string;
    message: string;
    args?: unknown[];
    colors?: boolean;
    colorTheme?: InterludeLogColorTheme;
    kaomoji?: boolean;
    standalone?: boolean;
}
export declare function renderLogMessage(message: string, args?: unknown[]): string;
export declare function detectLogAction(message: string, level: InterludeLogLevel): InterludeLogAction;
export declare function formatLayeredLog(input: LayeredLogInput): string;
export declare function phaseLabel(phase?: NarrativePhase): string;
