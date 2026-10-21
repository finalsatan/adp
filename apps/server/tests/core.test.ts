import { createServer } from 'node:http';
import { describe, expect, it } from 'vitest';
import {
  createDemoKnowledgeBase,
  dbOf,
  setupAdmin,
  startTestServer,
  uploadTextDocument,
  waitFor,
  type TestServer,
} from './helpers.js';

function multipartFile(
  fileName: string,
  mime: string,
  content: Buffer,
): { payload: Buffer; headers: Record<string, string> } {
  const boundary = `----adp-test-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  const head = Buffer.from(
    `--${boundary}\r\nContent-Disposition: form-data; name="file"; filename="${fileName}"\r\nContent-Type: ${mime}\r\n\r\n`,
  );
  const tail = Buffer.from(`\r\n--${boundary}--\r\n`);
  return {
    payload: Buffer.concat([head, content, tail]),
    headers: { 'content-type': `multipart/form-data; boundary=${boundary}` },
  };
}

async function uploadMultipart(
  server: TestServer,
  kbId: string,
  fileName: string,
  mime: string,
  content: Buffer,
) {
  const form = multipartFile(fileName, mime, content);
  return server.client.request('POST', `/api/knowledge-bases/${kbId}/documents`, form.payload, {
    headers: form.headers,
  });
}

describe('ADP backend integration', () => {
  it('initializes through nested setup transaction and enforces document type/source safety', async () => {
    const server = await startTestServer();
    try {
      const setup = await setupAdmin(server.client);
      expect(setup.user.isSystemAdmin).toBe(true);
      expect(setup.organizations).toHaveLength(1);
      const duplicate = await server.client.request(
        'POST',
        '/api/auth/setup',
        { name: '重复', email: 'other@example.test', password: 'Password123!', orgName: '另一个组织' },
        { expected: 409 },
      );
      expect(duplicate.data.error).toBe('CONFLICT');
      const models = (await server.client.request('GET', '/api/models')).data.items;
      expect(models.filter((model: { provider: string }) => model.provider === 'demo')).toHaveLength(2);
      const { kb } = await createDemoKnowledgeBase(server.client);

      const html = await server.client.request('POST', `/api/knowledge-bases/${kb.id}/documents`, {
        name: 'unsafe.html',
        content: '<html><body><script>alert(1)</script></body></html>',
      });
      const source = await server.client.request('GET', `/api/documents/${html.data.id}/source`);
      expect(source.status).toBe(200);
      expect(String(source.headers['content-disposition'])).toContain('attachment');
      expect(String(source.headers['content-security-policy'])).toContain("default-src 'none'");
      expect(String(source.headers['x-content-type-options'])).toBe('nosniff');

      const spoofedPdf = await server.client.request(
        'POST',
        `/api/knowledge-bases/${kb.id}/documents`,
        { name: 'fake.pdf', content: 'not a pdf' },
        { expected: 400 },
      );
      expect(spoofedPdf.data.error).toBe('INVALID_FILE_SIGNATURE');

      const spoofedText = await server.client.request(
        'POST',
        `/api/knowledge-bases/${kb.id}/documents`,
        { name: 'fake.txt', content: '%PDF-1.7\nmalicious' },
        { expected: 400 },
      );
      expect(spoofedText.data.error).toBe('MIME_MISMATCH');

      const badDocx = await server.client.request(
        'POST',
        `/api/knowledge-bases/${kb.id}/documents`,
        { name: 'fake.docx', content: 'plain text', mime: 'application/octet-stream' },
        { expected: 400 },
      );
      expect(badDocx.data.error).toBe('INVALID_FILE_SIGNATURE');
      await server.app.adp.worker.waitForIdle(5000);
    } finally {
      await server.close();
    }
  });

  it('does not silently reindex on chunk config save and blocks embedding changes with documents', async () => {
    const server = await startTestServer();
    try {
      await setupAdmin(server.client);
      const { kb } = await createDemoKnowledgeBase(server.client);
      const uploaded = await uploadTextDocument(server.client, kb.id);
      const ready = await waitFor(
        async () => (await server.client.request('GET', `/api/documents/${uploaded.data.id}`)).data,
        (doc) => doc.status === 'READY' || doc.status === 'FAILED',
      );
      expect(ready.status).toBe('READY');
      const before = (await server.client.request('GET', `/api/documents/${uploaded.data.id}/chunks`)).data.items;

      const patched = await server.client.request('PATCH', `/api/knowledge-bases/${kb.id}`, {
        chunkConfig: { strategy: 'structure', maxChars: 300, overlap: 20 },
      });
      expect(patched.data.chunkConfig.maxChars).toBe(300);
      const after = (await server.client.request('GET', `/api/documents/${uploaded.data.id}`)).data;
      expect(after.status).toBe('READY');
      expect(after.chunkCount).toBe(before.length);
      const queued = dbOf(server)
        .prepare("SELECT COUNT(*) AS n FROM jobs WHERE status IN ('QUEUED','RUNNING')")
        .get() as { n: number };
      expect(queued.n).toBe(0);

      await server.client.request('POST', `/api/documents/${uploaded.data.id}/reprocess`, undefined, {
        expected: 200,
      });
      await waitFor(
        async () => (await server.client.request('GET', `/api/documents/${uploaded.data.id}`)).data,
        (doc) => doc.status === 'READY',
      );

      const secondEmbedding = await server.client.request('POST', '/api/models', {
        name: '第二个演示向量',
        kind: 'embedding',
        provider: 'demo',
        model: 'demo-embedding-2',
        dimensions: 128,
        scope: 'organization',
      });
      const blocked = await server.client.request(
        'PATCH',
        `/api/knowledge-bases/${kb.id}`,
        { embeddingModelId: secondEmbedding.data.id },
        { expected: 409 },
      );
      expect(blocked.data.message).toContain('不能直接更换 Embedding');
      const empty = await server.client.request('POST', '/api/knowledge-bases', { name: '空库' });
      const changed = await server.client.request('PATCH', `/api/knowledge-bases/${empty.data.id}`, {
        embeddingModelId: secondEmbedding.data.id,
      });
      expect(changed.data.embeddingModelId).toBe(secondEmbedding.data.id);
    } finally {
      await server.close();
    }
  });

  it('uses the immutable publish snapshot and records model context in trace', async () => {
    const server = await startTestServer();
    try {
      await setupAdmin(server.client);
      const { kb, chat } = await createDemoKnowledgeBase(server.client);
      const uploaded = await uploadTextDocument(server.client, kb.id);
      await waitFor(
        async () => (await server.client.request('GET', `/api/documents/${uploaded.data.id}`)).data,
        (doc) => doc.status === 'READY',
      );
      const app = await server.client.request('POST', '/api/apps', {
        name: '快照助手',
        visibility: 'organization',
        kbIds: [kb.id],
        chatModelId: chat.id,
        systemPrompt: '发布时的提示',
        retrievalConfig: { mode: 'keyword', topK: 3, threshold: 0 },
      });
      await server.client.request('POST', `/api/apps/${app.data.id}/publish`);
      await server.client.request('PATCH', `/api/apps/${app.data.id}`, {
        name: '草稿名',
        kbIds: [],
        systemPrompt: '草稿提示',
        retrievalConfig: { mode: 'keyword', topK: 1, threshold: 0.99 },
      });
      const published = await server.client.request('GET', `/api/published/apps/${app.data.id}`);
      expect(published.data.name).toBe('快照助手');

      const answer = await server.client.request('POST', `/api/apps/${app.data.id}/chat`, {
        message: '一线城市住宿上限是多少？',
      });
      expect(answer.data.message.citations.length).toBeGreaterThan(0);
      expect(answer.data.message.content).toContain('600');
      const trace = await server.client.request('GET', `/api/traces/${answer.data.traceId}`);
      const context = trace.data.spans.find((span: { name: string }) => span.name === 'context');
      expect(context).toBeTruthy();
      expect(context.output.segments.length).toBeGreaterThan(0);
      expect(context.output.segments[0].index).toBe(1);
      expect(context.output.segments[0].chunkId).toBe(answer.data.message.citations[0].chunkId);
      expect(context.output.segments[0].content).toContain('600');
      expect(context.output.segments[0].position).toBeTypeOf('number');

      const draft = await server.client.request('POST', `/api/apps/${app.data.id}/chat`, {
        message: '一线城市住宿上限是多少？',
        draft: true,
      });
      expect(draft.data.refused).toBe(true);
      expect(draft.data.message.citations).toEqual([]);
    } finally {
      await server.close();
    }
  });

  it('does not let a stale ingest overwrite or fail a newer document version', async () => {
    let releaseParser: (() => void) | undefined;
    let parserCalls = 0;
    const parserGate = new Promise<void>((resolve) => {
      releaseParser = resolve;
    });
    const parser = createServer(async (req, res) => {
      const chunks: Buffer[] = [];
      for await (const chunk of req) chunks.push(chunk as Buffer);
      parserCalls += 1;
      await parserGate;
      const result = {
        schemaVersion: 1,
        text: '旧版本内容：住宿上限为500元。',
        elements: [
          {
            id: 'p1',
            type: 'paragraph',
            text: '旧版本内容：住宿上限为500元。',
            page: 1,
            order: 0,
          },
        ],
        engine: 'fake-parser',
        warnings: [],
      };
      res.writeHead(200, { 'content-type': 'application/json' }).end(JSON.stringify(result));
    });
    await new Promise<void>((resolve) => parser.listen(0, '127.0.0.1', resolve));
    const port = (parser.address() as { port: number }).port;
    const server = await startTestServer({ parserUrl: `http://127.0.0.1:${port}` });
    try {
      await setupAdmin(server.client);
      const { kb } = await createDemoKnowledgeBase(server.client);
      const pdfBytes = Buffer.from('%PDF-1.4\nfake old version\n');
      const created = await uploadMultipart(server, kb.id, 'old.pdf', 'application/pdf', pdfBytes);
      expect(created.status).toBe(200);
      await waitFor(async () => parserCalls, (count) => count >= 1, 5000);

      const replaced = await server.client.request('PUT', `/api/documents/${created.data.id}/file`, {
        name: 'new.md',
        content: '# 新版本\n\n新版本住宿上限为700元。',
      });
      expect(replaced.data.version).toBe(2);
      releaseParser?.();
      const ready = await waitFor(
        async () => (await server.client.request('GET', `/api/documents/${created.data.id}`)).data,
        (doc) => doc.status === 'READY' && doc.version === 2,
        10_000,
      );
      expect(ready.version).toBe(2);
      const chunks = (await server.client.request('GET', `/api/documents/${created.data.id}/chunks`)).data.items;
      expect(chunks.some((chunk: { content: string }) => chunk.content.includes('700'))).toBe(true);
      expect(chunks.some((chunk: { content: string }) => chunk.content.includes('旧版本'))).toBe(false);
    } finally {
      await server.close();
      await new Promise<void>((resolve) => parser.close(() => resolve()));
    }
  });

  it('keeps eval retries idempotent and reports null metrics when labels are missing', async () => {
    let chatCalls = 0;
    let injectedFailure = false;
    const provider = createServer(async (req, res) => {
      const chunks: Buffer[] = [];
      for await (const chunk of req) chunks.push(chunk as Buffer);
      const body = JSON.parse(Buffer.concat(chunks).toString() || '{}');
      if (req.url === '/v1/chat/completions') {
        chatCalls += 1;
        if (chatCalls === 2 && !injectedFailure) {
          injectedFailure = true;
          res.writeHead(500, { 'content-type': 'application/json' }).end(JSON.stringify({ error: 'transient' }));
          return;
        }
        res.writeHead(200, { 'content-type': 'application/json' }).end(
          JSON.stringify({
            choices: [{ message: { content: '住宿上限为600元。[1]' } }],
            usage: { prompt_tokens: 5, completion_tokens: 5, total_tokens: 10 },
          }),
        );
        return;
      }
      res.writeHead(404).end();
    });
    await new Promise<void>((resolve) => provider.listen(0, '127.0.0.1', resolve));
    const port = (provider.address() as { port: number }).port;
    const server = await startTestServer({ retryDelayMs: 10 });
    try {
      await setupAdmin(server.client);
      const { kb, chat: demoChat } = await createDemoKnowledgeBase(server.client);
      const uploaded = await uploadTextDocument(server.client, kb.id);
      await waitFor(
        async () => (await server.client.request('GET', `/api/documents/${uploaded.data.id}`)).data,
        (doc) => doc.status === 'READY',
      );

      const demoApp = await server.client.request('POST', '/api/apps', {
        name: '无标注评测',
        chatModelId: demoChat.id,
        kbIds: [kb.id],
        retrievalConfig: { mode: 'keyword', topK: 3, threshold: 0 },
      });
      const demoSet = await server.client.request('POST', '/api/eval-sets', {
        name: '无标注集',
        appId: demoApp.data.id,
      });
      await server.client.request('POST', `/api/eval-sets/${demoSet.data.id}/cases`, {
        question: '一线城市住宿上限是多少？',
        expectedAnswer: '',
      });
      const demoRun = await server.client.request('POST', `/api/eval-sets/${demoSet.data.id}/runs`, {
        configA: { mode: 'keyword', topK: 3, threshold: 0 },
      });
      const noLabels = await waitFor(
        async () => (await server.client.request('GET', `/api/eval-runs/${demoRun.data.id}`)).data,
        (run) => run.status === 'COMPLETED' || run.status === 'FAILED',
      );
      expect(noLabels.status, JSON.stringify(noLabels)).toBe('COMPLETED');
      expect(noLabels.metricsA.answerMatch).toBeNull();
      expect(noLabels.metricsA.citationAccuracy).toBeNull();
      expect(noLabels.metricsA.retrievalRecall).toBeNull();
      expect(noLabels.metricsA.denominators.answerMatch).toBe(0);
      expect(noLabels.metricsA.denominators.citationAccuracy).toBe(0);
      expect(noLabels.metricsA.denominators.retrievalRecall).toBe(0);

      const realModel = await server.client.request('POST', '/api/models', {
        name: '重试测试模型',
        kind: 'chat',
        provider: 'openai',
        baseUrl: `http://127.0.0.1:${port}/v1`,
        model: 'retry-chat',
        scope: 'organization',
      });
      const realApp = await server.client.request('POST', '/api/apps', {
        name: '重试评测',
        chatModelId: realModel.data.id,
        kbIds: [kb.id],
        retrievalConfig: { mode: 'keyword', topK: 3, threshold: 0 },
      });
      const realSet = await server.client.request('POST', '/api/eval-sets', {
        name: '重试集',
        appId: realApp.data.id,
      });
      await server.client.request('POST', `/api/eval-sets/${realSet.data.id}/cases`, {
        cases: [
          { question: '住宿上限是多少？', expectedAnswer: '600元', documentId: uploaded.data.id },
          { question: '一线城市住宿标准？', expectedAnswer: '600元', documentId: uploaded.data.id },
        ],
      });
      const retryRun = await server.client.request('POST', `/api/eval-sets/${realSet.data.id}/runs`, {
        configA: { mode: 'keyword', topK: 3, threshold: 0 },
      });
      const completed = await waitFor(
        async () => (await server.client.request('GET', `/api/eval-runs/${retryRun.data.id}`)).data,
        (run) => run.status === 'COMPLETED' || run.status === 'FAILED',
        15_000,
      );
      expect(completed.status, JSON.stringify(completed)).toBe('COMPLETED');
      expect(completed.results).toHaveLength(2);
      const stored = dbOf(server)
        .prepare('SELECT COUNT(*) AS n FROM eval_results WHERE run_id = ?')
        .get(retryRun.data.id) as { n: number };
      expect(stored.n).toBe(2);
      expect(chatCalls).toBe(4);
    } finally {
      await server.close();
      await new Promise<void>((resolve) => provider.close(() => resolve()));
    }
  });
});
