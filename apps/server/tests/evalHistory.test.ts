import { describe, expect, it } from 'vitest';
import {
  createDemoKnowledgeBase,
  setupAdmin,
  startTestServer,
  uploadTextDocument,
  waitFor,
} from './helpers.js';

describe('evaluation history snapshots', () => {
  it('keeps completed results after case deletion and freezes cases at enqueue time', async () => {
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
        name: '历史快照应用',
        chatModelId: chat.id,
        kbIds: [kb.id],
        retrievalConfig: { mode: 'keyword', topK: 3, threshold: 0 },
      });
      const set = await server.client.request('POST', '/api/eval-sets', {
        name: '历史快照集',
        appId: app.data.id,
      });
      const created = await server.client.request('POST', `/api/eval-sets/${set.data.id}/cases`, {
        cases: [
          { question: '住宿上限是多少？', expectedAnswer: '600元', documentId: uploaded.data.id },
          { question: '一线城市住宿标准？', expectedAnswer: '600元' },
        ],
      });
      const [firstCase, secondCase] = created.data.items;

      const run = await server.client.request('POST', `/api/eval-sets/${set.data.id}/runs`, {
        configA: { mode: 'keyword', topK: 3, threshold: 0 },
      });
      const completed = await waitFor(
        async () => (await server.client.request('GET', `/api/eval-runs/${run.data.id}`)).data,
        (value) => value.status === 'COMPLETED' || value.status === 'FAILED',
      );
      expect(completed.status).toBe('COMPLETED');
      expect(completed.results).toHaveLength(2);

      await server.client.request('DELETE', `/api/eval-cases/${firstCase.id}`);
      const afterDeleteCase = await server.client.request('GET', `/api/eval-runs/${run.data.id}`);
      expect(afterDeleteCase.data.results).toHaveLength(2);
      const preserved = afterDeleteCase.data.results.find(
        (result: { caseId: string }) => result.caseId === firstCase.id,
      );
      expect(preserved.expectedAnswer).toBe('600元');
      expect(preserved.question).toBe('住宿上限是多少？');

      // Freeze the case list at enqueue time: pause the worker, enqueue a run,
      // delete the remaining live case, then let the worker execute the snapshot.
      await server.app.adp.worker.stop();
      const frozenRun = await server.client.request('POST', `/api/eval-sets/${set.data.id}/runs`, {
        name: '冻结用例运行',
        configA: { mode: 'keyword', topK: 3, threshold: 0 },
      });
      await server.client.request('DELETE', `/api/eval-cases/${secondCase.id}`);
      const emptyCases = await server.client.request('GET', `/api/eval-sets/${set.data.id}/cases`);
      expect(emptyCases.data.items).toHaveLength(0);
      server.app.adp.worker.start();
      const frozenCompleted = await waitFor(
        async () => (await server.client.request('GET', `/api/eval-runs/${frozenRun.data.id}`)).data,
        (value) => value.status === 'COMPLETED' || value.status === 'FAILED',
      );
      expect(frozenCompleted.status).toBe('COMPLETED');
      expect(frozenCompleted.results).toHaveLength(1);
      expect(frozenCompleted.results[0].caseId).toBe(secondCase.id);
      expect(frozenCompleted.results[0].question).toBe('一线城市住宿标准？');
      const casesNow = await server.client.request('GET', `/api/eval-sets/${set.data.id}/cases`);
      expect(casesNow.data.items).toHaveLength(0);
    } finally {
      await server.close();
    }
  });
});
