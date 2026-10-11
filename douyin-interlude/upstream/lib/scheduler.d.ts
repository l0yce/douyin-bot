/**
 * P1: Scheduler——从 service.ts 抽出的调度状态管理。
 *
 * 拥有三块状态：
 *  - wakeTimers: 每故事一个“最早到期唤醒”（keep-earliest 仲裁；fire 返回 'busy'
 *    时按 retryDelayMs 自动重排，而不是等下一个常规 sweep）
 *  - exclusive: 独占任务去重门（同 key 任务在途时不再入队；可 halted 放弃让出门，
 *    defer 等待前台回合结束后再跑）
 *  - backoffs: 指纹冷却表（同一失败范围在 until 之前不重试）
 * 执行体（模型调用/投递/持久化）留在 service，经 fire/task 回调交互。
 */
export type MaybePromise<T> = T | Promise<T>;
export interface DueIntentWake {
    cancel: () => void;
    dueAt: number;
}
export interface BackoffEntry {
    fingerprint: string;
    until: number;
}
/** 最小依赖集——不含到期判断/模型调用/持久化（那些留在 service 的回调里）。 */
export interface SchedulerDeps {
    setTimeout: (fn: () => void, ms: number) => () => void;
    /** 注入时钟（测试用）；缺省 Date.now。 */
    now?: () => number;
    /** 唤醒遇 busy 后的重排间隔（service 传 Time.second）。 */
    retryDelayMs: number;
    /** 独占任务等待前台回合的重试间隔（service 传 500）。 */
    deferDelayMs: number;
    onWakeError: (error: unknown) => void;
    onTaskError: (error: unknown) => void;
}
export interface ExclusiveControls {
    /** true = 放弃本次任务并释放去重门（暂停/数据库重置）。 */
    halted: () => boolean;
    /** true = 让出执行权，deferDelayMs 后重试（前台回合进行中）。 */
    defer: () => boolean;
    /** 入队成功后、首次运行前回调（打点“已排队”日志）。 */
    onQueued?: () => void;
}
export interface Scheduler {
    /** 仲裁并安排唤醒；返回实际排定的延迟 ms，已有更早唤醒时返回 undefined 且不动现有定时器。 */
    scheduleWake(storyId: string, notBefore: Date, fire: () => MaybePromise<'busy' | void>): number | undefined;
    cancelWake(storyId: string): void;
    /** 取消全部（storyId 缺省）或指定故事的唤醒。 */
    cancelWakes(storyId?: string): void;
    pendingWakeCount(): number;
    /** 去重门 + 等待循环；返回 false 表示同 key 任务已在途。 */
    scheduleExclusive(key: string, task: () => Promise<unknown>, controls: ExclusiveControls): boolean;
    hasExclusive(key: string): boolean;
    isBackedOff(key: string, fingerprint: string, now?: number): boolean;
    noteBackoff(key: string, fingerprint: string, until: number): void;
    clearBackoff(key?: string): void;
}
export declare function createScheduler(deps: SchedulerDeps): Scheduler;
