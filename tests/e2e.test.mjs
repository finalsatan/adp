import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { createServer } from 'node:http';
import { execFileSync } from 'node:child_process';
import { apiClient, bootParser, bootServer, eventually, root } from './helpers/runtime.mjs';

test('持久化入库、发布问答、反馈、两配置评测、绑定 Key 与重启恢复', { timeout: 120000 }, async t => {
  const runtime = await bootServer();
  t.after(() => runtime.close());
  const client = apiClient(runtime.baseUrl);
  const call = async (method, route, body, options) => (await client.request(method, route, body, options)).data;
  assert.equal((await call('GET', '/api/auth/status')).initialized, false);
  const session = await call('POST', '/api/auth/setup', {
    name: '系统验收员', email: 'acceptance@example.test', password: 'AcceptancePass2026!', orgName: '试验组织',
  });
  assert.equal(session.user.isSystemAdmin, true);
  client.setOrg(session.organizations[0].id);
  assert.equal((await call('GET', '/api/auth/me')).user.id, session.user.id);
  await call('POST', '/api/auth/setup', {
    name: '重复初始化', email: 'other@example.test', password: 'AcceptancePass2026!', orgName: '另一个组织',
  }, { status: 409 });

  const models = (await call('GET', '/api/models')).items;
  const embedding = models.find(model => model.kind === 'embedding' && model.provider === 'demo');
  const chat = models.find(model => model.kind === 'chat' && model.provider === 'demo');
  assert.ok(embedding && chat, '首次初始化应提供明确标记的离线演示模型');
  const config = { mode: 'keyword', topK: 3, threshold: 0 };
  const kb = await call('POST', '/api/knowledge-bases', {
    name: '验收知识库', description: '虚构制度', visibility: 'organization', embeddingModelId: embedding.id,
    chunkConfig: { strategy: 'structure', maxChars: 500, overlap: 50 }, retrievalConfig: config,
  });
  const source = await readFile(path.join(root, 'examples/费用报销.md'), 'utf8');
  const document = await call('POST', `/api/knowledge-bases/${kb.id}/documents`, { name: '费用报销.md', content: source });
  const ready = await eventually(() => call('GET', `/api/documents/${document.id}`), d => d.status === 'READY' || d.status === 'FAILED');
  assert.equal(ready.status, 'READY', ready.error);
  assert.ok(ready.chunkCount > 0);
  const chunks = (await call('GET', `/api/documents/${document.id}/chunks`)).items;
  assert.ok(chunks.some(chunk => chunk.headingPath.length > 0));
  assert.ok(chunks.some(chunk => Object.keys(chunk.sourceRef).length > 0));
  const search = await call('POST', `/api/knowledge-bases/${kb.id}/search`, { query: '一线城市每人每晚住宿上限', config });
  assert.ok(search.items.some(hit => hit.content.includes('600')));
  assert.ok(search.stages.some(stage => stage.name === 'keyword'));

  const app = await call('POST', '/api/apps', {
    name: '费用助手', description: '验收用', visibility: 'organization', kbIds: [kb.id], chatModelId: chat.id,
    systemPrompt: '仅根据知识库回答，每个结论标注引用。', retrievalConfig: config,
  });
  const publication = await call('POST', `/api/apps/${app.id}/publish`);
  assert.equal(publication.version, 1);
  assert.ok(publication.embedCode.includes('iframe'));
  await call('PATCH', `/api/apps/${app.id}`, { name: '草稿改名', kbIds: [] });
  assert.equal((await call('GET', `/api/published/apps/${app.id}`)).name, '费用助手', '修改草稿不能影响发布内容');
  const answer = await call('POST', `/api/apps/${app.id}/chat`, { message: '一线城市每人每晚住宿上限是多少？' });
  assert.ok(answer.message.content.includes('600'));
  assert.ok(answer.message.citations.length > 0);
  const citation = answer.message.citations[0];
  assert.equal(citation.documentId, document.id);
  assert.ok(await call('GET', `/api/citations/${citation.chunkId}?appId=${app.id}`));
  const history = (await call('GET', `/api/conversations/${answer.conversationId}/messages`)).items;
  assert.equal(history.filter(message => message.role === 'assistant').length, 1);
  const trace = await call('GET', `/api/traces/${answer.traceId}`);
  assert.ok(trace.spans.length > 0);
  assert.equal(trace.status, 'OK');

  await call('POST', `/api/messages/${answer.message.id}/feedback`, { rating: -1, correction: '一线城市住宿上限是每人每晚600元。' });
  const feedback = (await call('GET', '/api/feedback')).items.find(item => item.messageId === answer.message.id);
  assert.equal(feedback.rating, -1);
  await call('PATCH', `/api/apps/${app.id}`, { kbIds: [kb.id] });
  const set = await call('POST', '/api/eval-sets', { name: '费用验收集', appId: app.id });
  await call('POST', `/api/eval-sets/${set.id}/from-feedback`, { feedbackId: feedback.id });
  await call('POST', `/api/eval-sets/${set.id}/cases`, {
    question: '一线城市住宿标准是多少？', expectedAnswer: '600 元', documentId: document.id, tags: ['表格'],
  });
  const evaluation = await call('POST', `/api/eval-sets/${set.id}/runs`, {
    name: '关键词与混合检索比较', configA: config, configB: { ...config, mode: 'hybrid' },
  });
  const run = await eventually(() => call('GET', `/api/eval-runs/${evaluation.id}`), r => r.status === 'COMPLETED' || r.status === 'FAILED', 40000);
  assert.equal(run.status, 'COMPLETED', run.error);
  assert.equal(run.results.length, 2);
  assert.equal(run.metricsA.total, 2);
  assert.equal(run.metricsB.total, 2);
  assert.equal(run.humanMetrics.A.reviewed, 0);
  assert.equal(run.humanMetrics.A.answerAccuracy, null);
  await call('PATCH', `/api/eval-results/${run.results[0].id}/review`, {
    variant: 'A', answerCorrect: true, citationsCorrect: false, comment: '答案正确，引用还需改进（测试记录）',
  });
  const reviewed = await call('GET', `/api/eval-runs/${run.id}`);
  assert.equal(reviewed.humanMetrics.A.reviewed, 1);
  assert.equal(reviewed.humanMetrics.A.total, 2);
  assert.equal(reviewed.humanMetrics.A.answerAccuracy, 1);
  assert.equal(reviewed.humanMetrics.A.citationAccuracy, 0);
  assert.equal(reviewed.humanMetrics.B.reviewed, 0);
  assert.equal(reviewed.results[0].reviews.A.reviewerName, '系统验收员');

  const key = await call('POST', '/api/api-keys', { name: '验收Key', appId: app.id });
  assert.ok(key.key.startsWith('adp_'));
  const anonymous = apiClient(runtime.baseUrl);
  const keyResponse = await anonymous.request('POST', `/api/apps/${app.id}/chat`, { message: '一线城市住宿标准？' }, { headers: { Authorization: `Bearer ${key.key}` } });
  assert.ok(keyResponse.data.message.citations.length > 0);
  await anonymous.request('GET', '/api/models', undefined, { headers: { Authorization: `Bearer ${key.key}` }, status: 403 });
  const listedKey = (await call('GET', '/api/api-keys')).items.find(item => item.id === key.id);
  assert.equal(listedKey.key, undefined);
  const db = new DatabaseSync(path.join(runtime.dataDir, 'adp.sqlite'), { readOnly: true });
  const persistedKey = db.prepare('SELECT key_hash FROM api_keys WHERE id=?').get(key.id);
  assert.notEqual(persistedKey.key_hash, key.key);
  db.close();
  await call('DELETE', `/api/api-keys/${key.id}`);
  await anonymous.request('POST', `/api/apps/${app.id}/chat`, { message: '住宿标准？' }, { headers: { Authorization: `Bearer ${key.key}` }, status: 401 });

  await runtime.restart();
  assert.equal((await call('GET', `/api/documents/${document.id}`)).status, 'READY');
  assert.equal((await call('GET', `/api/conversations/${answer.conversationId}/messages`)).items.length, 2);
  const audit = (await call('GET', '/api/audit')).items;
  assert.ok(audit.some(item => item.action.includes('publish')));
  assert.ok(audit.some(item => item.action.includes('key')));
});

test('真实 OpenAI 协议适配、SSE、密钥加密与失败重处理保留旧索引', { timeout: 120000 }, async t => {
  const secret = 'sk-adp-test-do-not-persist-plaintext';
  const requests = [];
  let failEmbedding = false;
  const provider = createServer(async (req, res) => {
    const pieces = [];
    for await (const piece of req) pieces.push(piece);
    const body = JSON.parse(Buffer.concat(pieces).toString() || '{}');
    requests.push({ path: req.url, authorization: req.headers.authorization, body });
    if (req.headers.authorization !== `Bearer ${secret}`) {
      res.writeHead(401, { 'content-type': 'application/json' }).end(JSON.stringify({ error: 'bad key' }));
      return;
    }
    const send = value => res.writeHead(200, { 'content-type': 'application/json' }).end(JSON.stringify(value));
    if (req.url === '/v1/embeddings') {
      if (failEmbedding) {
        res.writeHead(503, { 'content-type': 'application/json' }).end(JSON.stringify({ error: `provider unavailable ${secret}` }));
        return;
      }
      send({ data: body.input.map((_, index) => ({ index, embedding: [1, 0.25, 0.5] })), usage: { prompt_tokens: 11, total_tokens: 11 } });
    } else if (req.url === '/v1/rerank') {
      send({ results: body.documents.map((_, index) => ({ index, relevance_score: 0.01 })) });
    } else if (req.url === '/v1/chat/completions') {
      const answer = body.response_format
        ? JSON.stringify({ cases: [{ question: '一线城市住宿标准是多少？', expectedAnswer: '600元' }] })
        : '一线城市住宿上限为每人每晚600元。[1]';
      if (body.stream) {
        res.writeHead(200, { 'content-type': 'text/event-stream' });
        for (const part of ['一线城市住宿', '上限为每人每晚600元。', '[1]']) {
          res.write(`data: ${JSON.stringify({ choices: [{ delta: { content: part } }] })}\n\n`);
        }
        res.write(`data: ${JSON.stringify({ choices: [], usage: { prompt_tokens: 23, completion_tokens: 12, total_tokens: 35 } })}\n\n`);
        res.end('data: [DONE]\n\n');
      } else send({ choices: [{ message: { content: answer } }], usage: { prompt_tokens: 23, completion_tokens: 12, total_tokens: 35 } });
    } else res.writeHead(404).end();
  });
  await new Promise(resolve => provider.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise(resolve => provider.close(resolve)));
  const runtime = await bootServer();
  t.after(() => runtime.close());
  const client = apiClient(runtime.baseUrl);
  const call = async (method, route, body, options) => (await client.request(method, route, body, options)).data;
  const setup = await call('POST', '/api/auth/setup', { name: '验收员', email: 'model@example.test', password: 'AcceptancePass2026!', orgName: '模型验收' });
  client.setOrg(setup.organizations[0].id);
  const baseUrl = `http://127.0.0.1:${provider.address().port}/v1`;
  const common = { provider: 'openai', baseUrl, apiKey: secret, scope: 'organization', enabled: true };
  const embedding = await call('POST', '/api/models', { ...common, name: '测试Embedding', kind: 'embedding', model: 'test-embedding', dimensions: 3 });
  const chat = await call('POST', '/api/models', { ...common, name: '测试Chat', kind: 'chat', model: 'test-chat' });
  const rerank = await call('POST', '/api/models', { ...common, name: '测试Rerank', kind: 'rerank', model: 'test-rerank' });
  assert.ok(!JSON.stringify(await call('GET', '/api/models')).includes(secret));
  const database = new DatabaseSync(path.join(runtime.dataDir, 'adp.sqlite'), { readOnly: true });
  assert.ok(!JSON.stringify(database.prepare('SELECT * FROM models').all()).includes(secret));
  database.close();
  const kb = await call('POST', '/api/knowledge-bases', { name: '模型协议库', embeddingModelId: embedding.id });
  const doc = await call('POST', `/api/knowledge-bases/${kb.id}/documents`, { name: '政策.md', content: '# 差旅\n\n一线城市住宿上限为每人每晚600元。' });
  const ready = await eventually(() => call('GET', `/api/documents/${doc.id}`), value => ['READY', 'FAILED'].includes(value.status));
  assert.equal(ready.status, 'READY', ready.error);
  const config = { mode: 'hybrid', topK: 3, threshold: 0 };
  const app = await call('POST', '/api/apps', { name: '协议测试助手', chatModelId: chat.id, kbIds: [kb.id], retrievalConfig: config });
  await call('POST', `/api/apps/${app.id}/publish`);
  const answer = await call('POST', `/api/apps/${app.id}/chat`, { message: '一线城市住宿标准？' });
  assert.ok(answer.message.citations.length > 0);
  const stream = await client.request('POST', `/api/apps/${app.id}/chat`, { message: '一线城市住宿标准？', stream: true, conversationId: answer.conversationId });
  assert.equal(typeof stream.data, 'string');
  assert.ok(stream.data.includes('event: delta'));
  assert.ok(stream.data.includes('event: done'));
  assert.ok(stream.data.includes('600'));
  const chatRequests = requests.filter(request => request.path === '/v1/chat/completions');
  assert.ok(chatRequests.some(request => request.body.stream === true));
  assert.ok(chatRequests.some(request => request.body.messages.filter(message => message.role === 'user').length >= 2), '多轮会话历史应发送给模型');

  const reranked = await call('POST', `/api/knowledge-bases/${kb.id}/search`, {
    query: '住宿标准', config: { ...config, mode: 'keyword', rerankModelId: rerank.id, threshold: 0.7 },
  });
  assert.equal(reranked.items.length, 0, '低于阈值的重排结果不得进入回答上下文');
  const set = await call('POST', '/api/eval-sets', { name: '生成用例验收', appId: app.id });
  await call('POST', `/api/eval-sets/${set.id}/generate`, { count: 1, kbId: kb.id });
  const generated = await eventually(() => call('GET', `/api/eval-sets/${set.id}/cases`), result => result.items.length === 1);
  assert.equal(generated.items[0].expectedAnswer, '600元');

  failEmbedding = true;
  await call('POST', `/api/documents/${doc.id}/reprocess`);
  const failed = await eventually(() => call('GET', `/api/documents/${doc.id}`), value => value.status === 'FAILED');
  assert.ok(failed.error.includes('503'));
  assert.ok(!failed.error.includes(secret));
  const retained = await call('POST', `/api/knowledge-bases/${kb.id}/search`, { query: '住宿', config: { ...config, mode: 'keyword' } });
  assert.ok(retained.items.some(hit => hit.content.includes('600')), '重处理失败后旧切片仍可提供检索');
  assert.ok(!JSON.stringify(await call('GET', '/api/traces')).includes(secret));
  assert.ok(!runtime.output.includes(secret));
});

test('组织树不隐式继承资源，多组织用户不能借用另一组织权限，显式知识库授权分级执行', { timeout: 60000 }, async t => {
  const runtime = await bootServer();
  t.after(() => runtime.close());
  const admin = apiClient(runtime.baseUrl);
  const call = async (method, route, body, options) => (await admin.request(method, route, body, options)).data;
  const setup = await call('POST', '/api/auth/setup', {
    name: '平台管理员', email: 'root@example.test', password: 'AcceptancePass2026!', orgName: '总部',
  });
  const parentId = setup.organizations[0].id;
  admin.setOrg(parentId);
  const parentKb = await call('POST', '/api/knowledge-bases', { name: '总部资料', visibility: 'organization' });
  const child = await call('POST', '/api/organizations', { name: '子部门', parentId });
  admin.setOrg(child.id);
  const childKb = await call('POST', '/api/knowledge-bases', { name: '子部门资料', visibility: 'organization' });
  const readerRole = await call('POST', '/api/roles', { name: '资料只读', permissions: ['knowledge:read', 'apps:read'] });
  const builderRole = await call('POST', '/api/roles', {
    name: '知识搭建者', permissions: ['knowledge:read', 'knowledge:write', 'knowledge:grant', 'apps:read'],
  });
  const reader = await call('POST', '/api/users', {
    name: '子部门读者', email: 'reader@example.test', password: 'AcceptancePass2026!', orgId: child.id, roleId: builderRole.id,
  });
  const multiUser = await call('POST', '/api/users', {
    name: '多组织成员', email: 'multi@example.test', password: 'AcceptancePass2026!', orgId: child.id, roleId: readerRole.id,
  });
  admin.setOrg(parentId);
  const parentAdminRole = (await call('GET', '/api/roles')).items.find(role => role.name === '组织管理员' && !role.global);
  assert.ok(parentAdminRole);
  await call('POST', `/api/organizations/${parentId}/members`, { userId: multiUser.id, roleId: parentAdminRole.id });

  const childClient = apiClient(runtime.baseUrl);
  await childClient.request('POST', '/api/auth/login', { email: 'reader@example.test', password: 'AcceptancePass2026!' });
  childClient.setOrg(child.id);
  const listed = (await childClient.request('GET', '/api/knowledge-bases')).data.items;
  assert.ok(listed.some(kb => kb.id === childKb.id));
  assert.ok(!listed.some(kb => kb.id === parentKb.id), '子组织不自动继承上级组织资源');
  await childClient.request('GET', `/api/knowledge-bases/${parentKb.id}`, undefined, { status: 403 });
  await childClient.request('POST', '/api/models', {
    name: '越权模型', kind: 'chat', model: 'forbidden-model', provider: 'openai', baseUrl: 'http://127.0.0.1:9/v1',
  }, { status: 403 });
  await childClient.request('GET', '/api/traces', undefined, { status: 403 });

  const multi = apiClient(runtime.baseUrl);
  await multi.request('POST', '/api/auth/login', { email: 'multi@example.test', password: 'AcceptancePass2026!' });
  multi.setOrg(parentId);
  await multi.request('PATCH', `/api/knowledge-bases/${childKb.id}`, { name: '借用总部权限修改' }, { status: 403 });
  await multi.request('POST', `/api/knowledge-bases/${childKb.id}/grants`, { userId: reader.id, level: 'edit' }, { status: 403 });

  await call('POST', `/api/knowledge-bases/${parentKb.id}/grants`, { userId: reader.id, level: 'view' });
  await childClient.request('GET', `/api/knowledge-bases/${parentKb.id}`);
  await childClient.request('PATCH', `/api/knowledge-bases/${parentKb.id}`, { name: '只读权限修改' }, { status: 403 });
  // An explicit edit grant grants editing the resource without elevating its holder to an organization administrator.
  await call('POST', `/api/knowledge-bases/${parentKb.id}/grants`, { userId: reader.id, level: 'edit' });
  await childClient.request('PATCH', `/api/knowledge-bases/${parentKb.id}`, { description: '显式编辑授权写入' });
  await childClient.request('POST', '/api/organizations', { name: '越权建组织' }, { status: 403 });
});

test('独立解析服务贯通 DOCX 上传、章节表格、引用定位和原文件读取', { timeout: 60000 }, async t => {
  const parser = await bootParser();
  t.after(() => parser.close());
  const runtime = await bootServer({ PARSER_URL: parser.baseUrl, PARSER_TOKEN: parser.token });
  t.after(() => runtime.close());
  const client = apiClient(runtime.baseUrl);
  const call = async (method, route, body, options) => (await client.request(method, route, body, options)).data;
  const setup = await call('POST', '/api/auth/setup', { name: 'Office验收', email: 'office@example.test', password: 'AcceptancePass2026!', orgName: '文档验证' });
  client.setOrg(setup.organizations[0].id);
  const kb = await call('POST', '/api/knowledge-bases', { name: '办公文档知识库' });
  const bytes = execFileSync(path.join(root, 'apps/parser/.venv/bin/python'), ['-c', [
    'from docx import Document', 'from io import BytesIO', 'import sys',
    "d=Document(); d.add_heading('差旅标准',1); d.add_paragraph('以下为住宿标准。')",
    "table=d.add_table(rows=2, cols=2); table.cell(0,0).text='城市'; table.cell(0,1).text='住宿上限'",
    "table.cell(1,0).text='一线城市'; table.cell(1,1).text='600元'",
    'buffer=BytesIO(); d.save(buffer); sys.stdout.buffer.write(buffer.getvalue())',
  ].join('\n')]);
  const form = new FormData();
  form.set('file', new Blob([bytes], { type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' }), '差旅标准.docx');
  const uploaded = await call('POST', `/api/knowledge-bases/${kb.id}/documents`, form);
  const document = await eventually(() => call('GET', `/api/documents/${uploaded.id}`), value => ['READY', 'FAILED'].includes(value.status));
  assert.equal(document.status, 'READY', document.error);
  assert.ok(document.parsedText.includes('600元'));
  const chunks = (await call('GET', `/api/documents/${uploaded.id}/chunks`)).items;
  assert.ok(chunks.some(chunk => chunk.headingPath.includes('差旅标准')));
  const search = await call('POST', `/api/knowledge-bases/${kb.id}/search`, { query: '一线城市住宿上限', config: { mode: 'keyword', topK: 5, threshold: 0 } });
  assert.ok(search.items.some(hit => hit.content.includes('600元')));
  const sourceResponse = await client.request('GET', `/api/documents/${uploaded.id}/source`);
  assert.equal(sourceResponse.status, 200);
  assert.equal(Number(sourceResponse.response.headers.get('content-length')), bytes.length);
});
