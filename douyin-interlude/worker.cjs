const fs = require('node:fs');
const { App } = require('koishi');
const hdsi = require('./upstream/lib');
const botPath = process.env.BOT_CONFIG || require('node:path').resolve(__dirname, '../douyin-friend-ai-bot/config/config.json');
const bot = JSON.parse(fs.readFileSync(botPath, 'utf8'));
const options = JSON.parse(fs.readFileSync(process.env.BRIDGE_CONFIG || './bridge-config.json', 'utf8'));
const story = options.story || {};
let persona = bot.prompts?.system_prompt || '';
if (bot.prompts?.system_prompt_file) persona = fs.readFileSync(require('node:path').resolve(require('node:path').dirname(botPath), '..', bot.prompts.system_prompt_file), 'utf8');
const base = bot.ai.base_url.replace(/\/$/, '');
const endpoint = base.endsWith('/chat/completions') ? base : base.endsWith('/v1') ? base + '/chat/completions' : base + '/v1/chat/completions';
const baseConfig = {
  storyDefaults: {
    characterName: story.characterName || options.characterName || bot.owner?.nickname || '抖音朋友',
    characterProfile: (story.characterProfile ?? persona) + (story.startingSituation ? '\n\n[管理员设置的剧情起点]\n' + story.startingSituation : ''),
    relationship: story.relationship ?? '按实际聊天逐渐建立关系，不凭空假定亲密关系。',
    world: story.world ?? '现代现实日常生活，通过抖音文字私信联系。未记录的真实往事、线下见面和对方资料不能凭空当作事实。',
    supportingCast: story.supportingCast || '', location: story.location || '',
    style: story.style ?? '中文口语，短句，自然接话。持续叙事只写入后台剧本，发送给用户的内容只包含聊天台词。',
    timezone: 'Asia/Shanghai',
  },
  model: {
    providers: [{ id: 'douyin-main', label: 'Existing Douyin model', enabled: true,
      mode: 'openai-compatible', endpoint, apiKey: bot.ai.api_key, model: bot.ai.model,
      useForMain: true, useForCompaction: true, useForAlter: true,useForWorldSeeding:true },
      ...(bot.ai.vision_model?[{id:'douyin-vision',label:'图片理解模型',enabled:true,mode:'openai-compatible',endpoint,
        apiKey:bot.ai.api_key,model:bot.ai.vision_model,useForMain:false,useForCompaction:false,useForAlter:false,useForVision:true}]:[])],
    mainMaxTokens: 0, mainTimeout: 120000, mainResponseFormat: 'json-object',
    vision: { enabled: !!bot.ai.vision_model,mode:'sidecar' }, compaction: { enabled: true }, embedding: { enabled: false },
  },
  runtime: {
    autoCreate: true, captureDirectMessages: true, userMessageDebounceSeconds: 2,
    allowProactiveMessages: options.allowProactiveMessages === true,
    autoAdvanceEnabled: options.autoAdvanceEnabled !== false,
    autoAdvanceIntervalMinutes: options.autoAdvanceIntervalMinutes || 120, autoAdvanceJitterMinutes: 10,
    conversationFollowUpMinutes: [], maxMessageCharacters: bot.reply?.max_reply_chars || 200,
    sweepIntervalMinutes: 1, splitReplyMessages: true,
  },
  sharedStory: { allowCrossConversationMessages: false, shareParticipantDetails: false },
  schedulePreplan: { enabled: true }, timelineDirector: { enabled: true },
  alterSystem: { enabled: true }, agency: { enabled: true, proactiveDailyCap: 3 },
  worldSeeder:{enabled:true,cadenceMinutes:120,dailyCap:2},
  memory: { enabled: true }, onebot: { enabled: false },
  logging: { level: 'info', verbosity: 'summary', colors: false, kaomoji: false,
    logMessageContent: false, logScriptPreview: false },
};
const config = hdsi.Config(require('./hdsi-admin.cjs').douyinOnly(require('./hdsi-admin.cjs').merge(baseConfig,options.hdsiOverrides||{}))); 
const app = new App({ baseDir: process.cwd() });
app.plugin(require('@koishijs/plugin-http').default || require('@koishijs/plugin-http'));
app.plugin(require('@koishijs/plugin-database-sqlite').default, { path: options.databasePath || 'data/interlude.db' });
if(config.browser.enabled){
  const executablePath=fs.readFileSync('data/browser-executable.txt','utf8').trim();
  const plugin=require('koishi-plugin-puppeteer');
  app.plugin(plugin.default||plugin,{executablePath,args:['--no-sandbox','--disable-dev-shm-usage']});
}
app.plugin(hdsi, config);
app.start().then(async () => {
  require('./message-grounding.cjs').install(app.interlude);
  // bridge-ready can precede database initialization. Gate host traffic on both.
  await app.database.get('interlude_story', {});
  // Explicit panel settings apply to the current story, not just the next new story.
  if (options.story) {
    const stories = await app.database.get('interlude_story', { status: 'active' });
    for (const current of stories) {
      await app.interlude.updateSetting(current, {
        character: { name: config.storyDefaults.characterName, profile: config.storyDefaults.characterProfile },
        world: config.storyDefaults.world, relationship: config.storyDefaults.relationship,
        supportingCast: config.storyDefaults.supportingCast, location: config.storyDefaults.location,
        style: config.storyDefaults.style, timezone: config.storyDefaults.timezone,
        perspective: config.storyDefaults.perspective,perspectives:config.storyDefaults.perspectives,
        supplementaryFacts:config.storyDefaults.supplementaryFacts,
      });
      const participants = await app.database.get('interlude_participant', { storyId: current.id });
      for (const participant of participants) await app.database.set('interlude_participant', { id: participant.id }, { relationship: config.storyDefaults.relationship });
    }
  }
  require('./hdsi-admin.cjs').install({app,config,hdsi,options});
  process.send?.({type:'host-media-policy',visionEnabled:config.model.vision.enabled});
  process.send?.({ type: 'host-worker-ready' });
}).catch(() => { console.error('Koishi initialization failed; see service diagnostics.'); process.exit(1); });
process.on('SIGTERM', async () => { await app.stop(); process.exit(0); });
