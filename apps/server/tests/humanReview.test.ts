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

describe('manual eval review', () => {
  it('records per-variant reviews, computes reviewed denominators, and enforces access/state rules', async () => {
    const server = await startTestServer();
    try {
      const setup = await setupAdmin(server.client);
      const roles = (await server.client.request('GET', '/api/roles')).data.items;
      const terminalRole = roles.find(
        (role: { name: string; global: boolean }) => !role.global && role.name === '终端用户',
      );
      await server.client.request('POST', '/api/users', {
        name: '评审越权用户',
        email: 'reviewer-no-perm@example.test',
        password: 'Password123!',
        orgId: setup.organizations[0].id,
        roleId: terminalRole.id,
      });
      const otherClient = new TestClient(server.app);
      await otherClient.request('POST', '/api/auth/login', {
        email: 'reviewer-no-perm@example.test',
        password: 'Password123!',
      });
      otherClient.setOrg(setup.organizations[0].id);

      const { kb, chat } = await createDemoKnowledgeBase(server.client);
      const doc = await uploadTextDocument(server.client, kb.id);
      await waitFor(
        async () => (await server.client.request('GET', `/api/documents/${doc.data.id}`)).data,
        (value) => value.status === 'READY',
      );
      const app = await server.client.request('POST', '/api/apps', {
        name: '人工评审应用',
        chatModelId: chat.id,
        kbIds: [kb.id],
        retrievalConfig: { mode: 'keyword', topK: 3, threshold: 0 },
      });
      const set = await server.client.request('POST', '/api/eval-sets', {
        name: '人工评审集',
        appId: app.data.id,
      });
      const cases = await server.client.request('POST', `/api/eval-sets/${set.data.id}/cases`, {
        cases: [
          { question: '住宿上限是多少？', expectedAnswer: '600元', documentId: doc.data.id },
          { question: '一线城市住宿标准？', expectedAnswer: '600元' },
        ],
      });
      const run = await server.client.request('POST', `/api/eval-sets/${set.data.id}/runs`, {
        name: '双配置评审',
        configA: { mode: 'keyword', topK: 3, threshold: 0 },
        configB: { mode: 'hybrid', topK: 3, threshold: 0 },
      });
      const completed = await waitFor(
        async () => (await server.client.request('GET', `/api/eval-runs/${run.data.id}`)).data,
        (value) => value.status === 'COMPLETED' || value.status === 'FAILED',
      );
      expect(completed.status).toBe('COMPLETED');
      expect(completed.results).toHaveLength(2);
      expect(completed.humanMetrics.A).toEqual({
        reviewed: 0,
        total: 2,
        answerAccuracy: null,
        citationAccuracy: null,
      });
      expect(completed.humanMetrics.B.reviewed).toBe(0);
      expect(completed.humanMetrics.B.total).toBe(2);
      const [result1, result2] = completed.results;
      expect(result1.reviews).toEqual({});

      const firstReview = await server.client.request(
        'PATCH',
        `/api/eval-results/${result1.id}/review`,
        { variant: 'A', answerCorrect: true, citationsCorrect: false, comment: '答案对，引用不全' },
      );
      expect(firstReview.data.review).toMatchObject({
        variant: 'A',
        answerCorrect: true,
        citationsCorrect: false,
        comment: '答案对，引用不全',
        reviewerName: '测试管理员',
      });
      expect(firstReview.data.review.reviewedAt).toBeTruthy();
      expect(firstReview.data.humanMetrics.A).toEqual({
        reviewed: 1,
        total: 2,
        answerAccuracy: 1,
        citationAccuracy: 0,
      });
      expect(firstReview.data.humanMetrics.B).toEqual({
        reviewed: 0,
        total: 2,
        answerAccuracy: null,
        citationAccuracy: null,
      });

      const rereview = await server.client.request(
        'PATCH',
        `/api/eval-results/${result1.id}/review`,
        { variant: 'A', answerCorrect: false, citationsCorrect: true, comment: '复评修正' },
      );
      expect(rereview.data.humanMetrics.A).toEqual({
        reviewed: 1,
        total: 2,
        answerAccuracy: 0,
        citationAccuracy: 1,
      });
      const reviewCount = dbOf(server)
        .prepare('SELECT COUNT(*) AS n FROM eval_result_reviews WHERE result_id = ? AND variant = ?')
        .get(result1.id, 'A') as { n: number };
      expect(reviewCount.n).toBe(1);

      const bReview = await server.client.request(
        'PATCH',
        `/api/eval-results/${result1.id}/review`,
        { variant: 'B', answerCorrect: true, citationsCorrect: true },
      );
      expect(bReview.data.humanMetrics.A.answerAccuracy).toBe(0);
      expect(bReview.data.humanMetrics.A.citationAccuracy).toBe(1);
      expect(bReview.data.humanMetrics.B).toEqual({
        reviewed: 1,
        total: 2,
        answerAccuracy: 1,
        citationAccuracy: 1,
      });

      const secondB = await server.client.request(
        'PATCH',
        `/api/eval-results/${result2.id}/review`,
        { variant: 'B', answerCorrect: true, citationsCorrect: false },
      );
      expect(secondB.data.humanMetrics.B).toEqual({
        reviewed: 2,
        total: 2,
        answerAccuracy: 1,
        citationAccuracy: 0.5,
      });

      const refreshed = await server.client.request('GET', `/api/eval-runs/${run.data.id}`);
      expect(refreshed.data.humanMetrics.B.answerAccuracy).toBe(1);
      const refreshedResult1 = refreshed.data.results.find((item: { id: string }) => item.id === result1.id);
      expect(refreshedResult1.reviews.A).toMatchObject({ answerCorrect: false, citationsCorrect: true });
      expect(refreshedResult1.reviews.B).toMatchObject({ answerCorrect: true, citationsCorrect: true });
      const refreshedResult2 = refreshed.data.results.find((item: { id: string }) => item.id === result2.id);
      expect(refreshedResult2.reviews.A).toBeUndefined();
      expect(refreshedResult2.reviews.B).toMatchObject({ answerCorrect: true, citationsCorrect: false });

      await otherClient.request(
        'PATCH',
        `/api/eval-results/${result1.id}/review`,
        { variant: 'A', answerCorrect: true, citationsCorrect: true },
        { expected: 403 },
      );

      const singleRun = await server.client.request('POST', `/api/eval-sets/${set.data.id}/runs`, {
        name: '仅A配置',
        configA: { mode: 'keyword', topK: 3, threshold: 0 },
      });
      const singleCompleted = await waitFor(
        async () => (await server.client.request('GET', `/api/eval-runs/${singleRun.data.id}`)).data,
        (value) => value.status === 'COMPLETED' || value.status === 'FAILED',
      );
      expect(singleCompleted.status).toBe('COMPLETED');
      expect(singleCompleted.humanMetrics.B).toBeUndefined();
      const noB = await server.client.request(
        'PATCH',
        `/api/eval-results/${singleCompleted.results[0].id}/review`,
        { variant: 'B', answerCorrect: true, citationsCorrect: true },
        { expected: 400 },
      );
      expect(noB.data.error).toBe('BAD_REQUEST');

      const db = dbOf(server);
      const setRow = db.prepare('SELECT * FROM eval_sets WHERE id = ?').get(set.data.id) as Record<string, unknown>;
      db.prepare(
        `INSERT INTO eval_runs
         (id, set_id, name, status, progress, config_a, config_b, app_snapshot, case_snapshot,
          metrics_a, metrics_b, error, created_at, started_at, finished_at)
         VALUES ('running-run', ?, '运行中评审', 'RUNNING', 10, '{}', NULL, '{}', '[]', NULL, NULL, NULL, ?, ?, NULL)`,
      ).run(set.data.id, new Date().toISOString(), new Date().toISOString());
      db.prepare(
        `INSERT INTO eval_results
         (id, run_id, case_id, question, expected_answer, answer_a, citations_a, scores_a, created_at)
         VALUES ('running-result', 'running-run', ?, '问题', '答案', '回答', '[]', '{}', ?)`,
      ).run((cases.data.items[0] as { id: string }).id, new Date().toISOString());
      void setRow;
      const notCompleted = await server.client.request(
        'PATCH',
        '/api/eval-results/running-result/review',
        { variant: 'A', answerCorrect: true, citationsCorrect: true },
        { expected: 409 },
      );
      expect(notCompleted.data.message).toContain('尚未完成');

      const audit = await server.client.request('GET', '/api/audit');
      expect(audit.data.items.some((item: { action: string }) => item.action === 'eval_review.upsert')).toBe(true);
    } finally {
      await server.close();
    }
  });
});
