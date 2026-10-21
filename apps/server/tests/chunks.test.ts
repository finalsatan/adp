import { describe, expect, it } from 'vitest';
import {
  createDemoKnowledgeBase,
  setupAdmin,
  startTestServer,
  uploadTextDocument,
  waitFor,
} from './helpers.js';

describe('chunk strategies and manual chunk operations', () => {
  it('supports semantic and parent-child strategies, and disable/split/merge changes retrieval', async () => {
    const server = await startTestServer();
    try {
      await setupAdmin(server.client);
      const models = (await server.client.request('GET', '/api/models')).data.items;
      const embedding = models.find((model: { kind: string }) => model.kind === 'embedding');

      const semanticKb = await server.client.request('POST', '/api/knowledge-bases', {
        name: '语义分块库',
        embeddingModelId: embedding.id,
        chunkConfig: { strategy: 'semantic', maxChars: 260, overlap: 20 },
        retrievalConfig: { mode: 'hybrid', topK: 3, threshold: 0 },
      });
      const semanticDoc = await uploadTextDocument(
        server.client,
        semanticKb.data.id,
        'semantic.md',
        '# 报销制度\n\n员工出差需要提前申请。住宿费用按城市等级限额。交通费用凭票据报销。\n\n# 审批流程\n\n部门负责人审批后提交财务。财务在五个工作日内完成复核。',
      );
      const semanticReady = await waitFor(
        async () => (await server.client.request('GET', `/api/documents/${semanticDoc.data.id}`)).data,
        (doc) => doc.status === 'READY' || doc.status === 'FAILED',
      );
      expect(semanticReady.status).toBe('READY');
      expect(semanticReady.chunkCount).toBeGreaterThan(0);

      const parentKb = await server.client.request('POST', '/api/knowledge-bases', {
        name: '父子分块库',
        embeddingModelId: embedding.id,
        chunkConfig: { strategy: 'parent-child', maxChars: 500, overlap: 40 },
        retrievalConfig: { mode: 'keyword', topK: 5, threshold: 0 },
      });
      const parentDoc = await uploadTextDocument(
        server.client,
        parentKb.data.id,
        'parent.md',
        '# 差旅\n\n一线城市住宿上限为每人每晚600元。\n\n## 交通\n\n高铁二等座可以报销。',
      );
      await waitFor(
        async () => (await server.client.request('GET', `/api/documents/${parentDoc.data.id}`)).data,
        (doc) => doc.status === 'READY',
      );
      const parentChunks = (await server.client.request('GET', `/api/documents/${parentDoc.data.id}/chunks`)).data.items;
      expect(parentChunks.some((chunk: { isParent: boolean }) => chunk.isParent)).toBe(true);
      expect(
        parentChunks.some(
          (chunk: { isParent: boolean; parentId: string | null }) => !chunk.isParent && Boolean(chunk.parentId),
        ),
      ).toBe(true);
      const parentHits = (
        await server.client.request('POST', `/api/knowledge-bases/${parentKb.data.id}/search`, {
          query: '住宿上限',
          config: { mode: 'keyword', topK: 5, threshold: 0 },
        })
      ).data.items;
      expect(parentHits.length).toBeGreaterThan(0);
      // Parent-child retrieval recalls children but returns parent context.
      expect(parentHits.every((hit: { isParent: boolean }) => hit.isParent)).toBe(true);
      expect(parentHits.some((hit: { content: string }) => hit.content.includes('600'))).toBe(true);

      const { kb } = await createDemoKnowledgeBase(server.client, '分块编辑库');
      const editDoc = await uploadTextDocument(
        server.client,
        kb.id,
        'chunk.md',
        '# 独特内容\n\n独特词QUARKXYZ只在这里出现一次。',
      );
      await waitFor(
        async () => (await server.client.request('GET', `/api/documents/${editDoc.data.id}`)).data,
        (doc) => doc.status === 'READY',
      );
      const chunks = (await server.client.request('GET', `/api/documents/${editDoc.data.id}/chunks`)).data.items;
      const target = chunks.find((chunk: { content: string }) => chunk.content.includes('QUARKXYZ'));
      expect(target).toBeTruthy();
      const searchBefore = await server.client.request('POST', `/api/knowledge-bases/${kb.id}/search`, {
        query: 'QUARKXYZ',
        config: { mode: 'keyword', topK: 3, threshold: 0 },
      });
      expect(searchBefore.data.items.length).toBeGreaterThan(0);

      await server.client.request('PATCH', `/api/chunks/${target.id}`, { enabled: false });
      const searchDisabled = await server.client.request('POST', `/api/knowledge-bases/${kb.id}/search`, {
        query: 'QUARKXYZ',
        config: { mode: 'keyword', topK: 3, threshold: 0 },
      });
      expect(searchDisabled.data.items).toHaveLength(0);

      await server.client.request('PATCH', `/api/chunks/${target.id}`, { enabled: true });
      const offset = Math.max(1, Math.floor(target.content.length / 2));
      const split = await server.client.request('POST', `/api/chunks/${target.id}/split`, { offset });
      expect(split.data.newChunk.id).toBeTruthy();
      expect(split.data.chunk.content + split.data.newChunk.content).toContain('QUARKXYZ');
      const afterSplit = (await server.client.request('GET', `/api/documents/${editDoc.data.id}/chunks`)).data.items;
      expect(afterSplit.length).toBe(chunks.length + 1);

      const merged = await server.client.request('POST', `/api/chunks/${target.id}/merge`, {
        nextChunkId: split.data.newChunk.id,
      });
      expect(merged.data.content).toContain('QUARKXYZ');
      const afterMerge = (await server.client.request('GET', `/api/documents/${editDoc.data.id}/chunks`)).data.items;
      expect(afterMerge.length).toBe(chunks.length);
      const searchAfter = await server.client.request('POST', `/api/knowledge-bases/${kb.id}/search`, {
        query: 'QUARKXYZ',
        config: { mode: 'keyword', topK: 3, threshold: 0 },
      });
      expect(searchAfter.data.items.length).toBeGreaterThan(0);
    } finally {
      await server.close();
    }
  });
});
