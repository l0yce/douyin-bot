import { Context, Schema } from 'koishi';
import { Config as InterludeConfig, InterludeService } from './service';
declare module 'koishi' {
    interface Context {
        interlude: InterludeService;
    }
}
export declare const name = "hds-interlude";
export declare const version = "1.0.1-rc28";
export declare const inject: {
    required: string[];
    optional: string[];
};
export declare const Config: Schema<InterludeConfig>;
export declare function apply(ctx: Context, config: InterludeConfig): void;
export declare function looksLikeInterludeCommand(content: string): boolean;
export * from './narrator';
export * from './service';
export * from './types';
