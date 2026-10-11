import type { InterludeService } from './service';
export type DesktopRuntimePhase = 'running' | 'muted' | 'paused';
export type DesktopDeliveryStatus = 'sent' | 'retryable-failed' | 'permanent-failed';
export interface DesktopInboundEvent {
    transport: 'snowluma' | 'onebot-external' | 'sandbox';
    accountKey: string;
    platform: string;
    selfId: string;
    senderId: string;
    senderName?: string;
    channelId?: string;
    kind: 'private' | 'group';
    content: string;
    occurredAt: string;
    quote?: unknown;
    imageSources?: string[];
    voice?: unknown;
    rawMessageId?: string;
}
export declare function installDesktopBridge(service: InterludeService): () => void;
