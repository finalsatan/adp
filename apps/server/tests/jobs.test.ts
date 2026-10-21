import { createServer } from 'node:http';
import { describe, expect, it } from 'vitest';
import {
  createDemoKnowledgeBase,
  dbOf,
  setupAdmin,
  startTestServer,
  TestClient,
  uploadTextDocument,
  waitFor,
} from './helpers.js';

describe('job status API', () => {
  it('returns persisted status and rejects users without access to the target resource', async () => {
    const server = await startTestServer();
    try {
      const setup = await setupAdmin(server.client);
      const roles = (await server.client.request('GET', '/api/roles')).data.items;
      const terminalRole = roles.find(
        (role: { name: string; global: boolean }) => !role.global && role.name === '终端用户',
      );
      const other = await server.client.request('POST', '/api/users', {
        name: '普通用户',
        email: 'plain@example.test',
        password: 'Password123!',
        orgId: setup.organizations[0].id,
        roleId: terminalRole.id,
      });
      const otherClient = new TestClient(server.app);
      await otherClient.request('POST', '/api/auth/login', {
        email: 'plain@example.test',
        password: 'Password123!',
      });
      otherClient.setOrg(setup.organizations[0].id);

      const { kb, chat } = await createDemoKnowledgeBase(server.client);
      const uploaded = await uploadTextDocument(server.client, kb.id);
      const ingestJobId = uploaded.data.jobId;
      expect(ingestJobId).toBeTruthy();
      const ingestJob = await server.client.request('GET', `/api/jobs/${ingestJobId}`);
      expect(ingestJob.data.type).toBe('ingest');
      expect(['QUEUED', 'RUNNING', 'COMPLETED', 'FAILED']).toContain(ingestJob.data.status);
      expect(ingestJob.data.attempts).toBeGreaterThanOrEqual(0);
      await otherClient.request('GET', `/api/jobs/${ingestJobId}`, undefined, { expected: 403 });
      await waitFor(
        async () => (await server.client.request('GET', `/api/jobs/${ingestJobId}`)).data,
        (job) => job.status === 'COMPLETED',
      );

      const app = await server.client.request('POST', '/api/apps', {
        name: '任务测试应用',
        chatModelId: chat.id,
        kbIds: [kb.id],
        retrievalConfig: { mode: 'keyword', topK: 3, threshold: 0 },
      });
      const set = await server.client.request('POST', '/api/eval-sets', {
        name: '任务测试集',
        appId: app.data.id,
      });
      await server.client.request('POST', `/api/eval-sets/${set.data.id}/cases`, {
        question: '住宿标准？',
        expectedAnswer: '600元',
      });
      const run = await server.client.request('POST', `/api/eval-sets/${set.data.id}/runs`, {
        configA: { mode: 'keyword', topK: 3, threshold: 0 },
      });
      const evalJob = dbOf(server)
        .prepare("SELECT id FROM jobs WHERE type = 'eval-run' ORDER BY created_at DESC LIMIT 1")
        .get() as { id: string };
      const evalStatus = await server.client.request('GET', `/api/jobs/${evalJob.id}`);
      expect(evalStatus.data.type).toBe('eval-run');
      await otherClient.request('GET', `/api/jobs/${evalJob.id}`, undefined, { expected: 403 });
      void run;
    } finally {
      await server.close();
    }
  });

  it('redacts provider secrets from failed job errors', async () => {
    const secret = 'sk-job-status-secret';
    const provider = createServer(async (req, res) => {
      for await (const _chunk of req) {
        // consume request body
      }
      res.writeHead(503, { 'content-type': 'application/json' }).end(
        JSON.stringify({ error: `provider unavailable ${secret}` }),
      );
    });
    await new Promise<void>((resolve) => provider.listen(0, '127.0.0.1', resolve));
    const port = (provider.address() as { port: number }).port;
    const server = await startTestServer({ retryDelayMs: 10 });
    try {
      await setupAdmin(server.client);
      const embedding = await server.client.request('POST', '/api/models', {
        name: '失败向量模型',
        kind: 'embedding',
        provider: 'openai',
        baseUrl: `http://127.0.0.1:${port}/v1`,
        apiKey: secret,
        model: 'broken-embedding',
        scope: 'organization',
      });
      const kb = await server.client.request('POST', '/api/knowledge-bases', {
        name: '失败任务库',
        embeddingModelId: embedding.data.id,
      });
      const uploaded = await uploadTextDocument(server.client, kb.data.id);
      const failed = await waitFor(
        async () => (await server.client.request('GET', `/api/jobs/${uploaded.data.jobId}`)).data,
        (job) => job.status === 'FAILED',
        15_000,
      );
      expect(failed.error).toContain('503');
      expect(failed.error).not.toContain(secret);
      expect(failed.attempts).toBe(2);
    } finally {
      await server.close();
      await new Promise<void>((resolve) => provider.close(() => resolve()));
    }
  });
});
