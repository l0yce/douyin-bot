const assert = require('node:assert/strict');
const path = require('node:path');
const Module = require('node:module');
const { buildSync } = require('esbuild');
const hdsi = require('../upstream/lib');

async function check() {
  const config = hdsi.Config(require('../hdsi-admin.cjs').douyinOnly({}));
  assert.equal(config.model.mainMaxTokens, 0);
  assert.equal(config.model.compaction.maxTokens, 0);
  assert.equal(config.alterSystem.maxTokens, 0);
  assert.equal(config.worldSeeder.maxTokens, 0);
  const result = buildSync({stdin: {contents:
    "export { OpenAICompatibleNarrator } from './upstream/src/narrator'; export { anthropicBody } from './upstream/src/anthropic'; export { resolveWorldSeederRuntime } from './upstream/src/world-seeder';",
    resolveDir: path.resolve(__dirname, '..'), loader: 'ts'},
    bundle: true, write: false, platform: 'node', format: 'cjs', packages: 'external'});
  const mod = new Module(__filename, module);
  mod.paths = module.paths;
  mod._compile(result.outputFiles[0].text, __filename);
  const {OpenAICompatibleNarrator, anthropicBody, resolveWorldSeederRuntime} = mod.exports;
  assert.equal(resolveWorldSeederRuntime({}).maxTokens, 0);
  assert.equal(resolveWorldSeederRuntime({maxTokens: 1234}).maxTokens, 1234);
  assert.throws(() => anthropicBody({messages: []}, {maxTokens: 0}), /Token/);
  assert.equal(anthropicBody({messages: [], max_tokens: 1234}, {}).max_tokens, 1234);
  for (const budget of [0, 1234]) {
    config.model.providers = [{label:'Example', enabled:true, mode:'openai-compatible',
      endpoint:'https://api.example.com/v1/chat/completions', apiKey:'example', model:'example-model',
      useForVision:true, maxTokens:budget}];
    let sent;
    const narrator = new OpenAICompatibleNarrator({http: {post: async (url, body) => {
      sent = body; return {choices:[{message:{content:'一个笑脸'}}]};
    }}}, config.model, true);
    const observations = await narrator.describeImages([{dataUri:'data:image/png;base64,aW1hZ2U='}], '你好');
    assert(observations.length);
    if (budget) assert.equal(sent.max_tokens, budget);
    else assert.equal(Object.hasOwn(sent, 'max_tokens'), false);
  }
  console.log('PASS: blank token budgets, explicit limits, vision requests, Anthropic validation');
}
check().catch(error => { console.error(error); process.exitCode = 1; });
