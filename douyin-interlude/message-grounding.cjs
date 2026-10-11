const RULE = '[真实聊天事实边界] 真实抖音好友不是可自由编造台词的配角。只能使用本轮实际收到的消息、当前图片的视觉观察，以及标为 user-message 的历史收取记录。剧本、回忆、关系摘要不是新来信的证据。不得为接话或收尾补写好友后来又发消息、表情包、图片、通知、已读或回应。写完主角本轮发送的台词后，不能继续替好友写反应；等平台真正收到下一条消息再写。没有图片及视觉观察时，不得描写表情包图案或配字。期待、猜测必须明确保持未发生。';
const compact = value => String(value || '').toLowerCase().replace(/[\s“”‘’"'，,。.!?！？：:；;（）()【】\[\]《》<>—-]/g, '');
const historical = text => /(?:想起|回想|记得|回忆|之前|先前|昨晚|昨天|上次|那天|曾经|早些时候).{0,35}(?:发来|发了|回了|回复|收到|表情包|消息)/.test(text);
function outgoingText(decision) {
  const reply = decision.interaction?.reply || decision.reply || decision.groupReply;
  return reply?.mode === 'immediate' ? String(reply.content || '') : '';
}
function evidence(request) {
  const live = request.phase === 'user-message';
  const current = live ? [request.userMessage || '', ...(request.visualObservations || [])] : [];
  const histories = (request.recentEntries || []).filter(e => e.kind === 'user-message' || e.kind === 'group-message');
  return {current, histories,
    hasCurrentMedia: live && (!!request.images?.length || !!request.visualObservations?.length || /\[(?:图片|表情包)\]/.test(request.userMessage || '')),
    hasCurrentVisual: live && (!!request.images?.length || !!request.visualObservations?.length || /视觉观察，可能有误|视觉模型的观察/.test(request.userMessage || ''))};
}
function inspectIncomingClaims(request, decision) {
  if (typeof decision.script !== 'string') return [];
  const entries = evidence(request);
  const people = [request.participant, ...(request.participants || [])].filter(Boolean);
  const reply = outgoingText(decision);
  const paragraphs = decision.script.split(/\n\s*\n/);
  let sentInThisPassage = false;
  const issues = [];
  for (const paragraph of paragraphs) {
    const past = historical(paragraph);
    const named = people.filter(p => p.displayName && compact(paragraph).includes(compact(p.displayName)));
    const hasExternalSpeaker = named.length || /(?:对方|对面|好友|朋友|那边|这人|那人)/.test(paragraph);
    const received = /(?:发来|发来了|发了|回了|回复了|又回|又发|甩来|甩了|收到|送来|传来|消息.{0,6}(?:是|写着)|配字)/.test(paragraph);
    const tentative = /(?:也许|或许|可能会|会不会|说不定|期待|希望|想着要是|猜测|并没有|没有收到|没发来|未收到|并未|不是真的|错误生成)/.test(paragraph);
    if (hasExternalSpeaker && received && !tentative) {
      const participantIds = new Set(named.map(p => p.id));
      const currentAllowed = !named.length || named.some(p => p.id === request.participant?.id);
      const sources = past
        ? entries.histories.filter(e => !participantIds.size || participantIds.has(e.participantId)).map(e => e.content).concat(currentAllowed ? entries.current : [])
        : currentAllowed ? entries.current : [];
      const sourceText = compact(sources.join('\n'));
      if (!past && sentInThisPassage) issues.push('本轮发送之后编造好友后续来信');
      if (!past && !sources.some(Boolean)) issues.push('没有收取记录却描写新来信');
      if (!past && /(?:表情包|贴纸|一张图片|一张照片|发.{0,6}图)/.test(paragraph) && (!currentAllowed || !entries.hasCurrentMedia))
        issues.push('没有当前图片证据却描写好友发来图片或表情包');
      if (!past && /(?:表情包|贴纸|图片|照片).{0,70}(?:配字|写着|画着|一只|歪着|张着|图案|画面)/.test(paragraph) && !entries.hasCurrentVisual)
        issues.push('没有当前视觉证据却补写图片或表情包细节');
      const quotes = [...paragraph.matchAll(/[“「『"]([^”」』"\n]{1,180})[”」』"]/g)].map(m => m[1]);
      for (const quote of quotes) {
        const key = compact(quote);
        // A paragraph may also quote the protagonist's actual structured reply.
        if (key && !sourceText.includes(key) && !compact(reply).includes(key))
          issues.push('好友消息或表情配字没有实际收取或视觉证据');
      }
    }
    if (reply && /(?:发出去之后|发出去后|发送后|发完消息|消息发出去|发送完)/.test(paragraph)) sentInThisPassage = true;
  }
  return [...new Set(issues)];
}
function groundedRequest(request, feedback='') {
  const current = evidence(request);
  const summary = `[本轮收取记录] phase=${request.phase}；本轮可用图片/视觉证据=${current.hasCurrentMedia ? '有' : '无'}；实际来信：${current.current.join('\n').slice(0,450) || '无新来信'}。不得生成这批来信之后的好友回应。`;
  const additions = [RULE, summary];
  if (feedback) additions.unshift('[未发布草稿纠错] ' + feedback + '。上份草稿未写入、未发送；重新创作同一事件，删除虚构来信，保留真实消息和主角自己的生活。');
  return {...request, story:{...request.story,setting:{...request.story.setting,
    supplementaryFacts:[...additions,...(request.story.setting.supplementaryFacts || [])].slice(0,20)}}};
}
function install(service) {
  if (service.__messageGroundingInstalled) return;
  const provider = service.getNarrator();
  service.setNarrator({async decide(request) {
    let feedback='', earlyVisible=false;
    for (let attempt=0;attempt<2;attempt++) {
      const next = groundedRequest(request,feedback);
      if(request.onEarlyReply)next.onEarlyReply=async reply=>{
        const accepted=await request.onEarlyReply(reply);if(accepted)earlyVisible=true;return accepted;
      };
      const result=await provider.decide(next);
      const problems=inspectIncomingClaims(request,result);
      if(!problems.length)return result;
      feedback=problems.join('；');
      console.warn('[HDSI 消息事实校验] 已拦截未发布草稿：'+feedback);
      if(earlyVisible)break; // Never retry after an early visible transport action.
    }
    const error=new Error('HDSI 消息事实校验失败：拒绝写入没有收取记录的好友来信，等待重试。');
    error.code='HDSI_INVENTED_INCOMING';throw error;
  }});
  service.__messageGroundingInstalled=true;
  console.info('[HDSI 消息事实校验] 已启用：真实好友来信必须有收取证据');
}
module.exports={RULE,evidence,inspectIncomingClaims,groundedRequest,install};
