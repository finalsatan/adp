import { describe, expect, it } from 'vitest';
import {
  createDemoKnowledgeBase,
  setupAdmin,
  startTestServer,
  uploadTextDocument,
  waitFor,
} from './helpers.js';

function markdownTable(rows: string[]): string {
  const header = '| 项目 | 说明 | 金额 |\n| --- | --- | --- |';
  return `# 长表测试\n\n${header}\n${rows.map((row) => `| ${row} | ${'说明'.repeat(120)} | 100 |`).join('\n')}`;
}

describe('table-aware chunking and parent context backfill', () => {
  it('keeps long table rows intact with repeated headers in structure and parent-child modes', async () => {
    const server = await startTestServer();
    try {
      await setupAdmin(server.client);
      const models = (await server.client.request('GET', '/api/models')).data.items;
      const embedding = models.find((model: { kind: string }) => model.kind === 'embedding');
      const rows = ['ROW-A', 'ROW-B', 'ROW-C'];
      const content = markdownTable(rows);

      const structureKb = await server.client.request('POST', '/api/knowledge-bases', {
        name: '长表结构库',
        embeddingModelId: embedding.id,
        chunkConfig: { strategy: 'structure', maxChars: 200, overlap: 10 },
        retrievalConfig: { mode: 'keyword', topK: 3, threshold: 0 },
      });
      const structureDoc = await uploadTextDocument(server.client, structureKb.data.id, 'long-table.md', content);
      await waitFor(
        async () => (await server.client.request('GET', `/api/documents/${structureDoc.data.id}`)).data,
        (doc) => doc.status === 'READY',
      );
      const structureChunks = (await server.client.request('GET', `/api/documents/${structureDoc.data.id}/chunks`))
        .data.items as Array<{ content: string }>;
      for (const row of rows) {
        const chunk = structureChunks.find((item) => item.content.includes(row));
        expect(chunk, `missing ${row}`).toBeTruthy();
        expect(chunk!.content).toContain('| 项目 | 说明 | 金额 |');
        expect(chunk!.content).toContain(`| ${row} |`);
        expect(chunk!.content.split('\n').some((line) => line.includes(row) && line.endsWith('| 100 |'))).toBe(true);
      }

      const csvKb = await server.client.request('POST', '/api/knowledge-bases', {
        name: '长表CSV库',
        embeddingModelId: embedding.id,
        chunkConfig: { strategy: 'structure', maxChars: 200, overlap: 10 },
      });
      const csvContent = [
        'name,description,amount',
        `CSV-A,${'说明'.repeat(120)},100`,
        `CSV-B,${'说明'.repeat(120)},200`,
      ].join('\n');
      const csvDoc = await uploadTextDocument(server.client, csvKb.data.id, 'long-table.csv', csvContent);
      await waitFor(
        async () => (await server.client.request('GET', `/api/documents/${csvDoc.data.id}`)).data,
        (doc) => doc.status === 'READY',
      );
      const csvChunks = (await server.client.request('GET', `/api/documents/${csvDoc.data.id}/chunks`)).data.items as Array<{
        content: string;
      }>;
      for (const row of ['CSV-A', 'CSV-B']) {
        const chunk = csvChunks.find((item) => item.content.includes(row));
        expect(chunk).toBeTruthy();
        expect(chunk!.content.startsWith('name,description,amount')).toBe(true);
      }

      const parentKb = await server.client.request('POST', '/api/knowledge-bases', {
        name: '长表父子库',
        embeddingModelId: embedding.id,
        chunkConfig: { strategy: 'parent-child', maxChars: 400, overlap: 20 },
        retrievalConfig: { mode: 'keyword', topK: 3, threshold: 0 },
      });
      const parentDoc = await uploadTextDocument(
        server.client,
        parentKb.data.id,
        'parent-table.md',
        markdownTable(['PARENT-A', 'PARENT-B']),
      );
      await waitFor(
        async () => (await server.client.request('GET', `/api/documents/${parentDoc.data.id}`)).data,
        (doc) => doc.status === 'READY',
      );
      const allChunks = (await server.client.request('GET', `/api/documents/${parentDoc.data.id}/chunks`)).data.items as Array<{
        id: string;
        content: string;
        isParent: boolean;
        parentId: string | null;
      }>;
      const children = allChunks.filter((chunk) => !chunk.isParent);
      expect(children.length).toBeGreaterThan(0);
      const tableChildren = children.filter((chunk) =>
        chunk.content.split('\n').some((line) => line.trim().startsWith('| PARENT-')),
      );
      expect(tableChildren.length).toBeGreaterThan(0);
      for (const child of tableChildren) {
        expect(child.content).toContain('| 项目 | 说明 | 金额 |');
      }

      const search = await server.client.request('POST', `/api/knowledge-bases/${parentKb.data.id}/search`, {
        query: 'PARENT-A',
        config: { mode: 'keyword', topK: 3, threshold: 0 },
      });
      expect(search.data.items.length).toBeGreaterThan(0);
      const parentHit = search.data.items[0];
      expect(parentHit.isParent).toBe(true);
      expect(parentHit.parentId).toBeNull();
      expect(parentHit.content).toContain('PARENT-A');
      expect(parentHit.content).toContain('| 项目 | 说明 | 金额 |');
      expect(search.data.stages.some((stage: { name: string }) => stage.name === 'parent')).toBe(true);

      const chatModel = models.find((model: { kind: string }) => model.kind === 'chat');
      const app = await server.client.request('POST', '/api/apps', {
        name: '父块上下文助手',
        kbIds: [parentKb.data.id],
        chatModelId: chatModel.id,
        retrievalConfig: { mode: 'keyword', topK: 3, threshold: 0 },
      });
      await server.client.request('POST', `/api/apps/${app.data.id}/publish`);
      const answer = await server.client.request('POST', `/api/apps/${app.data.id}/chat`, {
        message: 'PARENT-A 的金额是多少？',
      });
      expect(answer.data.message.citations.length).toBeGreaterThan(0);
      const citation = answer.data.message.citations[0];
      expect(citation.chunkId).toBe(parentHit.id);
      expect(citation.content).toContain('PARENT-A');
      expect(citation.content).toContain('| 项目 | 说明 | 金额 |');
      const citationDetail = await server.client.request(
        'GET',
        `/api/citations/${citation.chunkId}?appId=${app.data.id}`,
      );
      expect(citationDetail.data.id).toBe(parentHit.id);
      expect(citationDetail.data.content).toBe(citation.content);
      expect(citationDetail.data.isParent).toBe(true);
      const trace = await server.client.request('GET', `/api/traces/${answer.data.traceId}`);
      const context = trace.data.spans.find((span: { name: string }) => span.name === 'context');
      expect(context.output.segments[0].chunkId).toBe(parentHit.id);
      expect(context.output.segments[0].content).toContain('PARENT-A');
    } finally {
      await server.close();
    }
  });
});
