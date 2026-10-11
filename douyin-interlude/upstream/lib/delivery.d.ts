import type { OutgoingMessageDraft } from './types';
import { ScriptEventDraft, ScriptMessageEventReference } from './script/contract';
export declare function attachMessageEvent(message: OutgoingMessageDraft, event: ScriptEventDraft | undefined, scriptEntryId?: number): OutgoingMessageDraft;
export declare function prepareOutgoingDelivery(message: OutgoingMessageDraft, bubbles: string[]): OutgoingMessageDraft | undefined;
export declare function scriptEventPayload(message: OutgoingMessageDraft, bubbleIndex?: number): {
    scriptEvent?: undefined;
} | {
    scriptEvent: {
        bubbleIndex: number;
        commitId: string;
        eventId: string;
        eventKind: "outgoing-message" | "group-message";
        causedByEventIds: string[];
        fullContent: string;
        bubbleCount: number;
    };
};
export declare function restoreMessageEvent(value: unknown, content: string): ScriptMessageEventReference | undefined;
export declare function deliveryEntryMetadata(message: OutgoingMessageDraft, extra?: Record<string, unknown>): {
    bubbleIndex?: number;
    commitId?: string;
    eventId?: string;
    eventKind?: "outgoing-message" | "group-message";
    causedByEventIds?: string[];
    fullContent?: string;
    bubbleCount?: number;
    visible: boolean;
    interaction: import("./types").NarrativeInteraction;
};
