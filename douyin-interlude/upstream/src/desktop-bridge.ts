/**
 * Optional typ-0 desktop bridge.
 *
 * It is enabled only inside a typ-0 worker. Regular Koishi/HDSI installations
 * keep their historical transport, scheduler and delivery behaviour unchanged.
 */
import { randomUUID } from 'node:crypto'
import type { Session } from 'koishi'
import type { DesktopTimelineRangeRequest, InterludeService } from './service'

export type DesktopRuntimePhase = 'running' | 'muted' | 'paused'
export type DesktopDeliveryStatus = 'sent' | 'retryable-failed' | 'permanent-failed'

export interface DesktopInboundEvent {
  transport: 'snowluma' | 'onebot-external' | 'sandbox'
  accountKey: string
  platform: string
  selfId: string
  senderId: string
  senderName?: string
  channelId?: string
  kind: 'private' | 'group'
  content: string
  occurredAt: string
  quote?: unknown
  imageSources?: string[]
  voice?: unknown
  rawMessageId?: string
}

type DesktopCommand =
  | { type: 'hdsi-desktop', command: 'phase', value: { requestId: string, phase: DesktopRuntimePhase } }
  | { type: 'hdsi-desktop', command: 'inbound', value: { requestId: string, event: DesktopInboundEvent } }
  | { type: 'hdsi-desktop', command: 'replay-inbox', value: { requestId: string, records: Array<{ id: string, event: DesktopInboundEvent }> } }
  | { type: 'hdsi-desktop', command: 'snapshot', value: { requestId: string } }
  | { type: 'hdsi-desktop', command: 'timeline-range', value: { requestId: string, query?: DesktopTimelineRangeRequest } }
  | { type: 'hdsi-desktop', command: 'delivery-result', value: { deliveryId: string, status: DesktopDeliveryStatus, messageIds?: string[], error?: string } }
  | { type: 'hdsi-desktop', command: 'purge-range', value: { requestId: string, from: string, to: string } }
  | { type: 'hdsi-desktop', command: 'cursor-set', value: { requestId: string, cursorAt: string } }

interface PendingDelivery {
  resolve: (messageIds: string[]) => void
  reject: (error: Error) => void
  timeout: ReturnType<typeof setTimeout>
}

interface DesktopDeliveryRequest {
  deliveryId: string
  accountKey: string
  transport: DesktopInboundEvent['transport']
  platform: string
  selfId: string
  channelId: string
  kind: 'private' | 'group'
  replyTo?: string
  content: string
  occurredAt: string
}

function isPhase(value: unknown): value is DesktopRuntimePhase {
  return value === 'running' || value === 'muted' || value === 'paused'
}

function isRequestId(value: unknown): value is string {
  return typeof value === 'string' && value.length >= 8 && value.length <= 128
}

function isInboundEvent(value: any): value is DesktopInboundEvent {
  return !!value && typeof value === 'object'
    && typeof value.accountKey === 'string' && typeof value.platform === 'string'
    && typeof value.selfId === 'string' && typeof value.senderId === 'string'
    && typeof value.content === 'string' && value.content.length <= 128_000
    && (value.kind === 'private' || value.kind === 'group')
    && (value.kind !== 'group' || typeof value.channelId === 'string')
    && !Number.isNaN(Date.parse(value.occurredAt))
}

function isTimelineRangeRequest(value: unknown): value is DesktopTimelineRangeRequest {
  if (value == null) return true
  if (!value || typeof value !== 'object') return false
  const query = value as DesktopTimelineRangeRequest
  if (query.from != null && (typeof query.from !== 'string' || Number.isNaN(Date.parse(query.from)))) return false
  if (query.to != null && (typeof query.to !== 'string' || Number.isNaN(Date.parse(query.to)))) return false
  if (query.tracks != null && (!Array.isArray(query.tracks) || query.tracks.length > 8 || query.tracks.some(track => typeof track !== 'string'))) return false
  if (query.cursor != null && (typeof query.cursor !== 'string' || query.cursor.length > 80)) return false
  if (query.limit != null && (!Number.isFinite(query.limit) || query.limit < 1 || query.limit > 500)) return false
  return query.detailLevel == null || query.detailLevel === 'summary' || query.detailLevel === 'full'
}

function sendToDesktop(event: string, payload: unknown) {
  if (typeof process.send !== 'function') return
  try { process.send({ type: 'hdsi-desktop', event, payload }) } catch { /* Parent can disappear during shutdown. */ }
}

function escapeAttribute(value: string) {
  return String(value).replace(/[&<>"']/g, character => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[character]!))
}

function desktopSession(event: DesktopInboundEvent, requestDelivery: (payload: Omit<DesktopDeliveryRequest, 'deliveryId' | 'occurredAt'>) => Promise<string[]>): Session {
  const channelId = event.kind === 'group'
    ? event.channelId!
    : event.channelId || `private:${event.senderId}`
  const content = [event.content, ...(event.imageSources ?? []).map(source => `<img src="${escapeAttribute(source)}">`)].filter(Boolean).join('')
  const session: Record<string, unknown> = {
    platform: event.platform,
    selfId: event.selfId,
    userId: event.senderId,
    username: event.senderName || event.senderId,
    channelId,
    guildId: event.kind === 'group' ? channelId : undefined,
    isDirect: event.kind === 'private',
    subtype: event.kind === 'private' ? 'private' : 'group',
    messageId: event.rawMessageId,
    content,
    timestamp: new Date(event.occurredAt).getTime(),
    quote: event.quote,
    send: async (outgoing: string) => requestDelivery({
      accountKey: event.accountKey, transport: event.transport, platform: event.platform,
      selfId: event.selfId, channelId, kind: event.kind, replyTo: event.rawMessageId,
      content: String(outgoing),
    }),
  }
  return session as unknown as Session
}

export function installDesktopBridge(service: InterludeService) {
  if (process.env.HDSI_DESKTOP_BRIDGE !== '1' || typeof process.send !== 'function') return undefined

  const pendingDeliveries = new Map<string, PendingDelivery>()
  const requestDelivery = (payload: Omit<DesktopDeliveryRequest, 'deliveryId' | 'occurredAt'>) => {
    const deliveryId = randomUUID()
    return new Promise<string[]>((resolve, reject) => {
      const timeout = setTimeout(() => {
        pendingDeliveries.delete(deliveryId)
        reject(new Error('等待 typ-0 渠道投递确认超时。'))
      }, 45_000)
      pendingDeliveries.set(deliveryId, { resolve, reject, timeout })
      sendToDesktop('delivery', { ...payload, deliveryId, occurredAt: new Date().toISOString() })
    })
  }
  const settleDelivery = (value: DesktopCommand['value']) => {
    if (!value || !('deliveryId' in value) || typeof value.deliveryId !== 'string') return false
    const pending = pendingDeliveries.get(value.deliveryId)
    if (!pending) return false
    pendingDeliveries.delete(value.deliveryId)
    clearTimeout(pending.timeout)
    if (value.status === 'sent') pending.resolve(Array.isArray(value.messageIds) ? value.messageIds.filter(id => typeof id === 'string') : [])
    else pending.reject(new Error(value.error || `渠道投递失败：${value.status}`))
    return true
  }

  service.setDesktopEventSink((event, payload) => sendToDesktop(event, payload))
  // typ-0 后台投递通道：delayed/split/advance 消息没有实时 Session，经宿主
  // Outbox/渠道适配器投递，delivery-result 回执与实时路径共用同一 Promise。
  // 普通 Koishi（无 bridge 环境）不会安装本桥，行为不变。
  service.setDesktopDeliveryHandler(delivery => new Promise<{ ok: boolean, messageIds?: string[], error?: string }>((resolve, reject) => {
    const deliveryId = randomUUID()
    const timeout = setTimeout(() => {
      pendingDeliveries.delete(deliveryId)
      reject(new Error('等待 typ-0 渠道投递确认超时。'))
    }, 45_000)
    pendingDeliveries.set(deliveryId, {
      resolve: ids => resolve({ ok: true, messageIds: Array.isArray(ids) ? ids.filter((id): id is string => typeof id === 'string') : [] }),
      reject: error => reject(error),
      timeout,
    })
    sendToDesktop('delivery', {
      deliveryId,
      accountKey: `desktop:${delivery.selfId}`,
      transport: 'onebot-external',
      platform: delivery.platform,
      selfId: delivery.selfId,
      channelId: delivery.channelId,
      kind: delivery.kind,
      replyTo: delivery.quoteMessageId,
      content: delivery.content,
      occurredAt: new Date().toISOString(),
    })
  }).catch(error => ({ ok: false as const, error: String(error) })))
  const initialPhase = isPhase(process.env.HDSI_PHASE) ? process.env.HDSI_PHASE : 'running'
  const heartbeat = setInterval(() => sendToDesktop('heartbeat', { phase: service.getDesktopRuntimePhase(), at: new Date().toISOString() }), 10_000)

  // M6：worker 把 loopback server 实际绑定的端口回传宿主（编译配置注入
  // port: 0，真实端口只有 worker 自己知道）。宿主据此登记 Console endpoint。
  // ctx 在 Service 基类是 protected，运行时通过只读探测拿到端口。
  const readServerPort = (): number | undefined => {
    const probe = service as unknown as { ctx?: { server?: { port?: number } } }
    const port = probe.ctx?.server?.port
    return Number.isInteger(port) && port > 0 ? port : undefined
  }
  const reportConsolePort = () => {
    const port = readServerPort()
    if (port) {
      sendToDesktop('console-port', { port, uiPath: '/console/' })
      return true
    }
    return false
  }
  // server 可能晚于本插件就绪：先立即探测，未就绪时短暂轮询。
  if (!reportConsolePort()) {
    const portPoller = setInterval(() => { if (reportConsolePort()) clearInterval(portPoller) }, 1_000)
    setTimeout(() => clearInterval(portPoller), 60_000)
  }

  const handle = async (message: unknown) => {
    const command = message as DesktopCommand
    if (!command || command.type !== 'hdsi-desktop') return
    if (command.command === 'delivery-result') {
      settleDelivery(command.value)
      return
    }
    try {
      if (command.command === 'phase') {
        if (!isRequestId(command.value?.requestId) || !isPhase(command.value?.phase)) throw new Error('无效 typ-0 运行状态请求。')
        await service.setDesktopRuntimePhase(command.value.phase)
        sendToDesktop('phase-result', { requestId: command.value.requestId, accepted: true, phase: command.value.phase })
        return
      }
      if (command.command === 'inbound') {
        if (!isRequestId(command.value?.requestId) || !isInboundEvent(command.value?.event)) throw new Error('无效 typ-0 入站事件。')
        const accepted = service.getDesktopRuntimePhase() === 'running'
          && await service.receiveDesktopEvent(command.value.event, desktopSession(command.value.event, requestDelivery))
        sendToDesktop('inbound-result', { requestId: command.value.requestId, accepted: !!accepted, error: accepted ? undefined : '当前剧本未接收该入站事件。' })
        return
      }
      if (command.command === 'cursor-set') {
        if (!isRequestId(command.value?.requestId) || typeof command.value?.cursorAt !== 'string') throw new Error('无效 typ-0 游标设置请求。')
        const cursorAt = new Date(command.value.cursorAt)
        if (Number.isNaN(cursorAt.getTime())) throw new Error('游标时间无法解析。')
        await service.setDesktopCursorAt(cursorAt)
        sendToDesktop('cursor-set-result', { requestId: command.value.requestId, accepted: true, cursorAt: cursorAt.toISOString() })
        return
      }
      if (command.command === 'replay-inbox') {
        if (!isRequestId(command.value?.requestId)) throw new Error('回放请求缺少 requestId。')
        const records = Array.isArray(command.value.records) ? command.value.records : []
        const results: Array<{ id: string, accepted: boolean, error?: string }> = []
        for (const record of records) {
          if (!record?.id || !isInboundEvent(record.event)) {
            results.push({ id: String(record?.id || ''), accepted: false, error: '无效收件箱记录。' })
            continue
          }
          try {
            const accepted = service.getDesktopRuntimePhase() === 'running'
              && await service.receiveDesktopEvent(record.event, desktopSession(record.event, requestDelivery))
            results.push({ id: record.id, accepted: !!accepted, error: accepted ? undefined : '当前剧本未接收该入站事件。' })
          } catch (error) {
            results.push({ id: record.id, accepted: false, error: String(error) })
          }
        }
        sendToDesktop('replay-result', { requestId: command.value.requestId, results })
        return
      }
      if (command.command === 'snapshot') {
        if (!isRequestId(command.value?.requestId)) throw new Error('快照请求缺少 requestId。')
        sendToDesktop('snapshot-result', { requestId: command.value.requestId, snapshot: await service.desktopTimelineSnapshot() })
        return
      }
      if (command.command === 'timeline-range') {
        if (!isRequestId(command.value?.requestId) || !isTimelineRangeRequest(command.value.query)) throw new Error('无效时间线范围请求。')
        sendToDesktop('timeline-range-result', { requestId: command.value.requestId, projection: await service.desktopTimelineRange(command.value.query) })
        return
      }
      if (command.command === 'purge-range') {
        // 选区删除：桌面 GUI 化的 purge。desktopPurgeRange 内部走 serial 队列
        // （与写作回合互斥）并复用成熟的 purgeStoryRange 软删语义。
        if (!isRequestId(command.value?.requestId)) throw new Error('选区删除请求缺少 requestId。')
        const from = new Date(String(command.value?.from ?? ''))
        const to = new Date(String(command.value?.to ?? ''))
        if (Number.isNaN(from.getTime()) || Number.isNaN(to.getTime()) || from > to) throw new Error('选区删除时间范围无效。')
        const result = await service.desktopPurgeRange(from, to)
        sendToDesktop('purge-range-result', { requestId: command.value.requestId, accepted: true, storyId: result.storyId })
        return
      }
    } catch (error) {
      const requestId = (command as any)?.value?.requestId
      const response = command.command === 'phase' ? 'phase-result'
        : command.command === 'inbound' ? 'inbound-result'
          : command.command === 'replay-inbox' ? 'replay-result'
            : command.command === 'snapshot' ? 'snapshot-result'
              : command.command === 'timeline-range' ? 'timeline-range-result'
                : command.command === 'purge-range' ? 'purge-range-result' : 'error'
      sendToDesktop(response, { requestId, accepted: false, error: String(error), results: command.command === 'replay-inbox' ? [] : undefined })
      sendToDesktop('error', { command: command.command, requestId, message: String(error) })
    }
  }

  process.on('message', handle)
  void service.setDesktopRuntimePhase(initialPhase)
    .catch(error => sendToDesktop('error', { command: 'initial-phase', message: String(error) }))
    // v4 adds deliveryActions/sceneCheckpoint fields to the timeline-range
    // projection and the host-side background delivery channel. Commands from
    // v2/v3 remain intact so an older desktop degrades gracefully.
    .finally(() => sendToDesktop('bridge-ready', { protocol: 4, phase: service.getDesktopRuntimePhase() }))
  return () => {
    clearInterval(heartbeat)
    service.setDesktopDeliveryHandler(undefined)
    process.off('message', handle)
    for (const pending of pendingDeliveries.values()) {
      clearTimeout(pending.timeout)
      pending.reject(new Error('typ-0 bridge 已关闭。'))
    }
    pendingDeliveries.clear()
  }
}
