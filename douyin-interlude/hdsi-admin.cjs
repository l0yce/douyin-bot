function douyinOnly(config){
 for(const key of ['onebot','chatActions','stickers','qzone'])config[key]={...(config[key]||{}),enabled:false};
 config.onebot.botAccounts=[];config.onebot.userAccounts=[];config.onebot.groupChats=[];
 config.chatActions.platforms=[];config.chatActions.quoteReply=false;config.chatActions.messageReactions=false;config.chatActions.nativeFaces=false;
 config.model={...(config.model||{}),audio:{...(config.model?.audio||{}),enabled:false}};
 for(const provider of config.model.providers||[])provider.useForStickers=false;
 config.runtime={...(config.runtime||{}),forwardMessage:{...(config.runtime?.forwardMessage||{}),enabled:false}};
 config.sharedStory={...(config.sharedStory||{}),managerAccounts:[]};
 return config;
}

const fs=require('node:fs'),path=require('node:path');
const SECRET='__KEEP_SECRET__';
const isSecret=key=>/^(apiKey|password|token|secret|extraHeaders|extraBody)$/i.test(key);
function redact(value,key=''){
  if(isSecret(key))return value ? SECRET : '';
  if(Array.isArray(value))return value.map(v=>redact(v));
  if(value&&typeof value==='object')return Object.fromEntries(Object.entries(value).map(([k,v])=>[k,redact(v,k)]));
  return value;
}
function restore(value,prior,key=''){
  if(isSecret(key)&&value===SECRET)return prior||'';
  if(Array.isArray(value))return value.map((v,i)=>restore(v,v?.id ? prior?.find(x=>x.id===v.id) : prior?.[i]));
  if(value&&typeof value==='object')return Object.fromEntries(Object.entries(value).map(([k,v])=>{
    if(['__proto__','prototype','constructor'].includes(k))throw new Error('Invalid property');
    return [k,restore(v,prior?.[k],k)];
  }));
  return value;
}
function merge(a,b){
  if(!b||typeof b!=='object'||Array.isArray(b))return b===undefined?a:b;
  const out={...(a||{})};
  for(const [k,v] of Object.entries(b)){
    if(['__proto__','prototype','constructor'].includes(k))throw new Error('Invalid property');
    out[k]=v&&typeof v==='object'&&!Array.isArray(v)?merge(out[k],v):v;
  }
  return out;
}
function install({app,config,hdsi,options}){
  const db=app.database,service=app.interlude;
  const tables=()=>Object.keys(app.model.tables).filter(k=>k.startsWith('interlude_'));
  const get=async(table,query={},limit=100)=>tables().includes(table)?db.get(table,query,{limit}):[];
  const current=async()=>{const rows=await get('interlude_story',{},100);return rows.find(s=>s.status==='active')||rows.find(s=>s.status==='paused');};
  const backup=async()=>{
    const data={exportedAt:new Date().toISOString(),tables:{}};
    for(const name of tables())data.tables[name]=await db.get(name,{});
    const directory='data/admin-backups';fs.mkdirSync(directory,{recursive:true,mode:0o700});
    const file=path.join(directory,Date.now()+'.json');fs.writeFileSync(file,JSON.stringify(data),{mode:0o600});return file;
  };
  async function handle(action,args={}){
    const story=await current();
    if(action==='config')return {config:redact(config),schema:hdsi.Config.toJSON(),version:hdsi.version,browserReady:fs.existsSync('data/browser-executable.txt')};
    if(action==='validate-config'){
      const incoming=restore(args.config,config);
      const validated=hdsi.Config(douyinOnly(incoming));
      if(!validated.model.providers.some(p=>p.enabled!==false&&p.useForMain&&p.apiKey&&p.model))throw new Error('至少保留一个有密钥的主叙事模型连接。');
      const bot=JSON.parse(fs.readFileSync(process.env.BOT_CONFIG||path.resolve(__dirname,'../douyin-friend-ai-bot/config/config.json'),'utf8'));
      if(validated.runtime.maxMessageCharacters>(bot.reply?.max_reply_chars||200))throw new Error('单条消息不能超过原机器人的字数限制：'+(bot.reply?.max_reply_chars||200)+' 字。');
      if(validated.browser.enabled&&!fs.existsSync('data/browser-executable.txt'))throw new Error('网页浏览器运行时尚未准备好。');
      return {validated}; // IPC only; host persists this, never returns secrets over HTTP.
    }
    if(action==='export'){
      const result={exportedAt:new Date().toISOString(),version:hdsi.version,tables:{}};
      for(const name of tables())result.tables[name]=await db.get(name,{});
      return result;
    }
    if(action==='overview'){
      if(!story)return {phase:service.getDesktopRuntimePhase(),story:null,participants:[],health:null};
      return {phase:service.getDesktopRuntimePhase(),story,participants:await service.participants(story.id,true),
        health:service.health.snapshot(story.id),schedule:await service.adminSchedulePreplan(story.id),
        intents:await service.adminPendingIntents(story.id,100),facts:await service.adminFacts(story.id,100),
        memories:await service.memories(story.id,100),overlay:await service.adminOverlayStatus(story.id),
        events:await get('interlude_seeded_event',{storyId:story.id},100),
        observations:await get('interlude_web_observation',{storyId:story.id},100)};
    }
    if(action==='timeline')return service.desktopTimelineRange({from:args.from,to:args.to,cursor:args.cursor,
      tracks:args.tracks,limit:Math.min(200,args.limit||50),detailLevel:'full'});
    if(action==='pause'||action==='resume'){
      const phase=action==='pause'?'paused':'running';await service.setDesktopRuntimePhase(phase);return {phase};
    }
    if(action==='start'){
      if(story)return {storyId:story.id,alreadyExists:true};
      const cfg=JSON.parse(fs.readFileSync(process.env.BOT_CONFIG||path.resolve(__dirname,'../douyin-friend-ai-bot/config/config.json'),'utf8'));
      const friend=cfg.whitelist.friends.find(f=>f.enabled!==false);
      if(!friend)throw new Error('请先添加一个白名单好友。');
      const created=await service.createStory({platform:'douyin',selfId:'douyin-main',userId:friend.sec_uid,
        username:friend.name,channelId:'private:'+friend.sec_uid,isDirect:true,content:''});
      return {storyId:created.id};
    }
    if(!story)throw new Error('还没有故事，请先启动故事或收到一条好友消息。');
    const text=String(args.content||'').trim();
    const id=Number(args.id);
    const destructive=['forget','cancel-intent','reject-patch','overlay-clear','rebase','purge-range','purge-platform','reset','clear-database'];
    let backupFile;
    if(destructive.includes(action)){
      if(args.confirm!=='确认')throw new Error('此操作需要输入“确认”。');
      backupFile=await backup();
    }
    if(['fact-add','note','fact-edit'].includes(action)&&(!text||text.length>8000))throw new Error('请输入 1 至 8000 字内容。');
    if(['forget','cancel-intent','reject-patch','fact-edit'].includes(action)&&(!Number.isInteger(id)||id<1))throw new Error('记录编号无效。');
    const result=async(value)=>({result:await value,backupFile});
    if(action==='fact-add'){
      if(!['character','world','relationship','event','promise'].includes(args.scope))throw new Error('记忆分类无效。');
      return result(service.serial(story.id,()=>service.addAdminFact(story,args.scope,text)));
    }
    if(action==='fact-edit')return result(service.serial(story.id,async()=>{
      const fact=(await db.get('interlude_fact',{id,storyId:story.id,status:'active'}))[0];
      if(!fact)throw new Error('这条记忆已不存在。');
      await service.addAdminFact(story,fact.scope,text);await service.forgetAdminFact(story.id,id);return true;
    }));
    if(action==='note')return result(service.serial(story.id,()=>service.addAdminScriptNote(story,text)));
    if(action==='forget')return result(service.serial(story.id,()=>service.forgetAdminFact(story.id,id)));
    if(action==='cancel-intent')return result(service.serial(story.id,()=>service.cancelAdminIntent(story.id,id)));
    if(action==='reject-patch')return result(service.serial(story.id,()=>service.rejectAdminStatePatch(story.id,id)));
    if(action==='compact')return result(service.compactStory(story));
    if(action==='overlay-compact')return result(service.compactOverlay(story));
    if(action==='overlay-clear'){
      if(!['character','perspective','relationship','world','all'].includes(args.target))throw new Error('演化层分类无效。');
      return result(service.clearSettingOverlay(story,args.target));
    }
    if(action==='schedule-refresh'){
      await service.requestSchedulePreplanRebuild(story.id);service.scheduleCompaction(story.id);return {queued:true};
    }
    if(action==='advance'){
      const messages=await service.advanceStory(story);const delivered=await service.deliverMessages(story,messages);
      return {generated:messages.length,delivered:delivered.length};
    }
    if(action==='rebase')return result(service.rebaseTimeline(story));
    if(action==='purge-range'){
      const from=new Date(args.from),to=new Date(args.to);
      if(!Number.isFinite(+from)||!Number.isFinite(+to)||from>to)throw new Error('请选择有效的起止时间。');
      return result(service.desktopPurgeRange(from,to));
    }
    if(action==='purge-platform'){
      if(args.platform!=='douyin')throw new Error('平台无效。');
      return result(service.purgePlatformData(args.platform));
    }
    if(action==='reset')return result(service.purgeAllStoryData(story.id));
    if(action==='clear-database')return result(service.clearDatabase());
    throw new Error('不支持此操作。');
  }
  process.on('message',async message=>{
    if(message?.type!=='hdsi-admin'||typeof message.requestId!=='string')return;
    try{process.send?.({type:'hdsi-admin-result',requestId:message.requestId,result:await handle(message.action,message.args)});}
    catch(error){process.send?.({type:'hdsi-admin-result',requestId:message.requestId,error:String(error.message||error)});}
  });
}
module.exports={install,merge,redact,restore,douyinOnly};
