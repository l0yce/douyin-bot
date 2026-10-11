const fs = require('node:fs');
const http = require('node:http');
const { fork } = require('node:child_process');
const { randomUUID, timingSafeEqual } = require('node:crypto');
const options = JSON.parse(fs.readFileSync(process.env.BRIDGE_CONFIG || './bridge-config.json', 'utf8'));
const botPath = process.env.BOT_CONFIG || require('node:path').resolve(__dirname, '../douyin-friend-ai-bot/config/config.json');
const statePath = options.statePath || 'data/bridge-state.json';
fs.mkdirSync(require('node:path').dirname(statePath), { recursive: true });
let state = fs.existsSync(statePath) ? JSON.parse(fs.readFileSync(statePath, 'utf8')) : { inbox: {}, outbox: {} };
function save() {
  const temp = statePath + '.tmp';
  fs.writeFileSync(temp, JSON.stringify(state), { mode: 0o600 });
  const fd = fs.openSync(temp, 'r'); fs.fsyncSync(fd); fs.closeSync(fd);
  fs.renameSync(temp, statePath);
}
// IPC promises cannot survive restart. Never re-send ambiguous deliveries.
for (const item of Object.values(state.outbox)) {
  if (item.status === 'pending') item.status = 'expired';
  if (item.status === 'sending') item.status = 'unknown';
}
for (const item of Object.values(state.inbox)) if (item.status === 'submitting') item.status = 'unknown';
save();
let workerReady = false, protocolReady = false, stopping = false;
let adminActive=false;
let mediaPolicy={visionEnabled:false};
const callbacks = new Map();
const worker = fork('./worker.cjs', [], {
  env: { ...process.env, HDSI_DESKTOP_BRIDGE: '1', HDSI_PHASE: options.runtimePhase || 'running' },
  execArgv: ['--max-old-space-size=256'], stdio: ['ignore', 'inherit', 'inherit', 'ipc'],
});
function send(command, value) { worker.send({ type: 'hdsi-desktop', command, value }); }
function allowed(senderId) {
  const config = JSON.parse(fs.readFileSync(botPath, 'utf8'));
  return (config.whitelist?.friends || []).some(f => f.enabled !== false && String(f.sec_uid) === senderId);
}
worker.on('message', message => {
  if(message.type==='host-media-policy'){mediaPolicy={visionEnabled:message.visionEnabled};return;}
  if(message.type==='hdsi-admin-result'){
    if(callbacks.has(message.requestId)){callbacks.get(message.requestId)(message);callbacks.delete(message.requestId);}
    return;
  }
  if (message.type === 'host-worker-ready') { workerReady = true; return; }
  if (message.type !== 'hdsi-desktop') return;
  const { event, payload } = message;
  if (event === 'bridge-ready') protocolReady = true;
  if (event === 'delivery') {
    const userId = String(payload.channelId).replace(/^private:/, '');
    const bot = JSON.parse(fs.readFileSync(botPath, 'utf8'));
    const plainText = !/<(?:img|image|audio|video|face|quote|file)\b/i.test(payload.content);
    const blocked = Object.values(state.outbox).some(item => item.senderId === userId && item.status === 'unknown');
    if (blocked || !allowed(userId) || payload.platform !== 'douyin' || payload.kind !== 'private'
      || !plainText || !payload.content.trim() || payload.content.length > (bot.reply?.max_reply_chars || 200)) {
      send('delivery-result', { deliveryId: payload.deliveryId, status: 'permanent-failed', error: 'Delivery rejected by bridge policy' });
      return;
    }
    state.outbox[payload.deliveryId] = { ...payload, senderId: userId,
      status: 'pending', createdAt: Date.now(), expiresAt: Date.now() + 290000 };
    save();
  }
  if (payload?.requestId && callbacks.has(payload.requestId)) {
    callbacks.get(payload.requestId)(payload); callbacks.delete(payload.requestId);
  }
});
worker.on('exit', () => {
  workerReady = false;
  if (!stopping) { console.error('Narrative worker exited; restarting service.'); process.exit(1); }
});
function reply(res, status, value) { res.writeHead(status, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(value)); }
function authenticated(req) {
  const supplied = Buffer.from(req.headers.authorization || '');
  const expected = Buffer.from('Bearer ' + options.token);
  return supplied.length === expected.length && timingSafeEqual(supplied, expected);
}
function adminRPC(action,args={}){
  return new Promise((resolve,reject)=>{
    const requestId=randomUUID();
    const timeout=setTimeout(()=>{callbacks.delete(requestId);reject(new Error('操作仍未返回，结果待核实。'));},240000);
    callbacks.set(requestId,result=>{clearTimeout(timeout);result.error?reject(new Error(result.error)):resolve(result.result);});
    worker.send({type:'hdsi-admin',requestId,action,args});
  });
}
const configPath=process.env.BRIDGE_CONFIG||'./bridge-config.json';
const revision=raw=>require('node:crypto').createHash('sha256').update(raw).digest('hex');
function writeOptions(value){const temp=configPath+'.tmp';fs.writeFileSync(temp,value,{mode:0o600});fs.renameSync(temp,configPath);}
function busy(){return adminActive||Object.values(state.outbox).some(x=>['pending','sending','unknown'].includes(x.status))
  ||Object.values(state.inbox).some(x=>['accepted','submitting'].includes(x.status)&&x.createdAt>Date.now()-180000);}
const server = http.createServer(async (req, res) => {
  if (!authenticated(req)) return reply(res, 401, { error: 'Unauthorized' });
  try {
    if (req.method === 'GET' && req.url === '/health') return reply(res, 200, {
      ready: workerReady && protocolReady, engine: 'HDS Interlude',
      phase: options.runtimePhase||'running',
      ...mediaPolicy,
      pending: Object.values(state.outbox).filter(x => ['pending', 'sending'].includes(x.status)).length,
      unknown: Object.values(state.outbox).filter(x => x.status === 'unknown').length,
    });
    if (req.method === 'GET' && req.url === '/outbox') {
      let changed = false;
      for (const item of Object.values(state.outbox)) if (['pending', 'sending'].includes(item.status) && item.expiresAt < Date.now()) {
        item.status = item.status === 'sending' ? 'unknown' : 'expired'; changed = true;
      }
      if (changed) save();
      return reply(res, 200, { deliveries: Object.values(state.outbox).filter(x => x.status === 'pending') });
    }
    if (req.method !== 'POST') return reply(res, 404, { error: 'Not found' });
    let body = '';
    for await (const chunk of req) { body += chunk; if (body.length > 8000000) return reply(res, 413, { error: 'Too large' }); }
    const data = JSON.parse(body);
    if(req.url==='/admin'){
      if(!workerReady||!protocolReady)return reply(res,503,{error:'剧情服务正在启动，请稍后重试。'});
      const reads=['overview','timeline','config','export'];
      const action=data.action;
      const actions=['start','pause','resume','advance','compact','overlay-compact','overlay-clear','schedule-refresh','fact-add','fact-edit','forget','note','cancel-intent','reject-patch','rebase','purge-range','purge-platform','reset','clear-database'];
      if(!reads.includes(action)&&!actions.includes(action))return reply(res,400,{error:'操作无效。'});
      if(!reads.includes(action)&&!/^[a-zA-Z0-9_-]{8,100}$/.test(data.operationId||''))return reply(res,400,{error:'操作编号无效。'});
      state.admin ||= {};
      if(!reads.includes(action)&&state.admin[data.operationId]){
        const known=state.admin[data.operationId];return reply(res,known.status==='complete'?200:409,known.status==='complete'?known.result:{error:'这次操作结果待核实，请不要重复执行。'});
      }
      if(!reads.includes(action)&&busy())return reply(res,409,{error:'消息正在处理，请稍后操作。'});
      if(!reads.includes(action)){state.admin[data.operationId]={status:'running',action,at:Date.now()};save();}
      if(!reads.includes(action))adminActive=true;
      try{
        const result=await adminRPC(action,data.args||{});
        if(action==='pause'||action==='resume'){
          options.runtimePhase=result.phase;const opts=JSON.parse(fs.readFileSync(configPath,'utf8'));opts.runtimePhase=result.phase;writeOptions(JSON.stringify(opts,null,2));
        }
        if(!reads.includes(action)){state.admin[data.operationId]={status:'complete',action,at:Date.now(),result};save();}
        return reply(res,200,result);
      }catch(error){
        if(!reads.includes(action)){state.admin[data.operationId].status='unknown';save();}
        return reply(res,400,{error: error.message});
      }finally{if(!reads.includes(action))adminActive=false;}
    }
    if(req.url==='/full-config'){
      if(busy())return reply(res,409,{error:'现在有消息正在处理，请稍后再保存。'});
      const raw=fs.readFileSync(configPath,'utf8');
      if(data.revision!==revision(raw))return reply(res,409,{error:'设置已更新，请重新读取后保存。'});
      let validated;
      try{validated=(await adminRPC('validate-config',{config:data.config})).validated;}
      catch{return reply(res,400,{error:'配置未通过 HDSI 校验，请检查字段、范围和主模型连接。'});}
      const backupName='backups/full-settings-'+Date.now()+'.json';
      fs.mkdirSync('backups',{recursive:true,mode:0o700});fs.writeFileSync(backupName,raw,{mode:0o600});
      const opts=JSON.parse(raw);opts.hdsiOverrides=validated;
      const sd=validated.storyDefaults;
      let profile=sd.characterProfile;
      const suffix='\n\n[管理员设置的剧情起点]\n'+(opts.story?.startingSituation||'');
      if(opts.story?.startingSituation){if(profile.endsWith(suffix))profile=profile.slice(0,-suffix.length);else opts.story.startingSituation='';}
      opts.story={...opts.story,characterName:sd.characterName,characterProfile:profile,world:sd.world,relationship:sd.relationship,
        supportingCast:sd.supportingCast,location:sd.location,style:sd.style};
      opts.allowProactiveMessages=validated.runtime.allowProactiveMessages;opts.autoAdvanceEnabled=validated.runtime.autoAdvanceEnabled;
      opts.autoAdvanceIntervalMinutes=validated.runtime.autoAdvanceIntervalMinutes;
      // Basic form owns these fields; keep the rest of the full schema as overrides.
      for(const key of ['characterName','characterProfile','world','relationship','supportingCast','location','style'])delete opts.hdsiOverrides.storyDefaults[key];
      for(const key of ['allowProactiveMessages','autoAdvanceEnabled','autoAdvanceIntervalMinutes'])delete opts.hdsiOverrides.runtime[key];
      writeOptions(JSON.stringify(opts,null,2));
      return reply(res,200,{saved:true,revision:revision(fs.readFileSync(configPath,'utf8')),previousBackup:backupName});
    }
    if (req.url === '/inbound') {
      if (!/^[a-zA-Z0-9_-]{8,100}$/.test(data.eventId || '') || !allowed(data.senderId)
        || typeof data.content !== 'string' || !data.content.trim() || data.content.length > 128000
        || !Number.isFinite(Date.parse(data.occurredAt))) return reply(res, 400, { error: 'Invalid event or recipient' });
      if(data.imageSources&&(!Array.isArray(data.imageSources)||data.imageSources.length>2||data.imageSources.some(x=>typeof x!=='string'||!/^data:image\/(png|jpeg|webp);base64,[A-Za-z0-9+/=]+$/.test(x))))return reply(res,400,{error:'Invalid image'});
      const existing = state.inbox[data.eventId];
      if (existing && existing.status !== 'rejected') return reply(res, 200, { status: existing.status, accepted: existing.status === 'accepted' });
      if (!workerReady || !protocolReady || adminActive) return reply(res, 503, { status: 'not-ready' });
      const requestId = randomUUID();
      state.inbox[data.eventId] = { status: 'submitting', createdAt: Date.now(), senderId: data.senderId };
      save();
      const timeout = setTimeout(() => {
        callbacks.delete(requestId);
        state.inbox[data.eventId].status = 'unknown'; save();
        if (!res.writableEnded) reply(res, 200, { status: 'unknown' });
      }, 25000);
      callbacks.set(requestId, result => {
        clearTimeout(timeout);
        state.inbox[data.eventId].status = result.accepted ? 'accepted' : 'rejected'; save();
        if (!res.writableEnded) reply(res, 200, { status: state.inbox[data.eventId].status, accepted: !!result.accepted });
      });
      send('inbound', { requestId, event: { transport: 'sandbox', accountKey: 'douyin-main', platform: 'douyin',
        selfId: options.selfId || 'douyin-main', senderId: data.senderId, senderName: data.senderName,
        channelId: 'private:' + data.senderId, kind: 'private', content: data.content,
        occurredAt: data.occurredAt, rawMessageId: data.eventId,imageSources:data.imageSources } });
      return;
    }
    if (req.url === '/claim' || req.url === '/result') {
      const item = state.outbox[data.deliveryId];
      if (!item) return reply(res, 404, { error: 'Unknown delivery' });
      if (req.url === '/claim') {
        if (item.status !== 'pending' || item.expiresAt < Date.now()) return reply(res, 409, { error: 'No longer pending' });
        item.status = 'sending'; save(); return reply(res, 200, { claimed: true });
      }
      if (!['sent', 'retryable-failed', 'permanent-failed'].includes(data.status)) return reply(res, 400, { error: 'Invalid receipt' });
      if (['sent', 'retryable-failed', 'permanent-failed'].includes(item.status)) return reply(res, 200, { recorded: true });
      // A late success would no longer resolve the worker promise. Stop rather than misrecord reality.
      if (!['sending', 'pending'].includes(item.status) || item.expiresAt < Date.now()) return reply(res, 409, { error: 'Expired receipt' });
      item.status = data.status; item.completedAt = Date.now(); save();
      send('delivery-result', { deliveryId: data.deliveryId, status: data.status,
        messageIds: data.status === 'sent' ? ['douyin-local:' + data.deliveryId] : [], error: data.error });
      return reply(res, 200, { recorded: true });
    }
    reply(res, 404, { error: 'Not found' });
  } catch { if (!res.writableEnded) reply(res, 500, { error: 'Bridge request failed' }); }
});
server.listen(options.port || 18765, '127.0.0.1', () => console.log('Douyin bridge listening on loopback'));
process.on('SIGTERM', () => { stopping = true; server.close(); worker.kill('SIGTERM'); setTimeout(() => process.exit(0), 3000); });
