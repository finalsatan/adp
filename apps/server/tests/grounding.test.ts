import { createServer } from 'node:http';
import { describe, expect, it } from 'vitest';
import { REFUSAL_ANSWER } from '../src/services/chat.js';
import {
  createDemoKnowledgeBase,
  setupAdmin,
  startTestServer,
  uploadTextDocument,
  waitFor,
} from './helpers.js';

describe('grounded answer validation for real models', () => {
  it('refuses answers without citations or with only invalid citations, and keeps valid ones', async () => {
    const provider = createServer(async (req, res) => {
      const chunks: Buffer[] = [];
      for await (const chunk of req) chunks.push(chunk as Buffer);
      const body = JSON.parse(Buffer.concat(chunks).toString() || '{}');
      if (req.url !== '/v1/chat/completions') {
        res.writeHead(404).end();
        return;
      }
      const question = (body.messages as Array<{ role: string; content: string }>)
        .filter((message) => message.role === 'user')
        .at(-1)?.content ?? '';
      let answer = '住宿上限为600元。[1]';
      if (question.includes('无引用')) answer = '这是一个没有引用的回答。';
      else if (question.includes('越界')) answer = '这是一个越界引用。[999]';
      else if (question.includes('混合')) answer = '住宿上限为600元。[1][999]';
      if (body.stream) {
        res.writeHead(200, { 'content-type': 'text/event-stream' });
        for (const part of answer.match(/.{1,8}/g) ?? []) {
          res.write(`data: ${JSON.stringify({ choices: [{ delta: { content: part } }] })}\n\n`);
        }
        res.write(
          `data: ${JSON.stringify({ choices: [], usage: { prompt_tokens: 4, completion_tokens: 4, total_tokens: 8 } })}\n\n`,
        );
        res.end('data: [DONE]\n\n');
        return;
      }
      res.writeHead(200, { 'content-type': 'application/json' }).end(
        JSON.stringify({
          choices: [{ message: { content: answer } }],
          usage: { prompt_tokens: 4, completion_tokens: 4, total_tokens: 8 },
        }),
      );
    });
    await new Promise<void>((resolve) => provider.listen(0, '127.0.0.1', resolve));
    const port = (provider.address() as { port: number }).port;
    const server = await startTestServer();
    try {
      await setupAdmin(server.client);
      const { kb } = await createDemoKnowledgeBase(server.client);
      const doc = await uploadTextDocument(server.client, kb.id);
      await waitFor(
        async () => (await server.client.request('GET', `/api/documents/${doc.data.id}`)).data,
        (value) => value.status === 'READY',
      );
      const model = await server.client.request('POST', '/api/models', {
        name: '引用校验模型',
        kind: 'chat',
        provider: 'openai',
        baseUrl: `http://127.0.0.1:${port}/v1`,
        model: 'grounding-chat',
        scope: 'organization',
      });
      const app = await server.client.request('POST', '/api/apps', {
        name: '引用校验助手',
        kbIds: [kb.id],
        chatModelId: model.data.id,
        retrievalConfig: { mode: 'keyword', topK: 3, threshold: 0 },
      });
      await server.client.request('POST', `/api/apps/${app.data.id}/publish`);

      const noCitation = await server.client.request('POST', `/api/apps/${app.data.id}/chat`, {
        message: '无引用：住宿上限是多少？',
      });
      expect(noCitation.data.refused).toBe(true);
      expect(noCitation.data.message.content).toBe(REFUSAL_ANSWER);
      expect(noCitation.data.message.citations).toEqual([]);

      const outOfRange = await server.client.request('POST', `/api/apps/${app.data.id}/chat`, {
        message: '越界：住宿上限是多少？',
      });
      expect(outOfRange.data.refused).toBe(true);
      expect(outOfRange.data.message.content).toBe(REFUSAL_ANSWER);
      expect(outOfRange.data.message.citations).toEqual([]);

      const valid = await server.client.request('POST', `/api/apps/${app.data.id}/chat`, {
        message: '正常：住宿上限是多少？',
      });
      expect(valid.data.refused).toBe(false);
      expect(valid.data.message.content).toContain('600');
      expect(valid.data.message.content).toContain('[1]');
      expect(valid.data.message.citations).toHaveLength(1);
      expect(valid.data.message.citations[0].documentId).toBe(doc.data.id);

      const mixed = await server.client.request('POST', `/api/apps/${app.data.id}/chat`, {
        message: '混合：住宿上限是多少？',
      });
      expect(mixed.data.refused).toBe(false);
      expect(mixed.data.message.content).toContain('[1]');
      expect(mixed.data.message.content).not.toContain('999');
      expect(mixed.data.message.citations).toHaveLength(1);

      const streamed = await server.client.request('POST', `/api/apps/${app.data.id}/chat`, {
        message: '越界：住宿上限是多少？',
        stream: true,
      });
      expect(streamed.text).toContain('event: delta');
      const doneBlock = streamed.text
        .split('\n\n')
        .find((block) => block.startsWith('event: done'));
      expect(doneBlock).toBeTruthy();
      const doneData = JSON.parse(
        doneBlock!.split('\n').find((line) => line.startsWith('data: '))!.slice('data: '.length),
      );
      expect(doneData.message.content).toBe(REFUSAL_ANSWER);
      expect(doneData.message.citations).toEqual([]);
      const citationsBlock = streamed.text
        .split('\n\n')
        .find((block) => block.startsWith('event: citations'));
      const citationsData = JSON.parse(
        citationsBlock!.split('\n').find((line) => line.startsWith('data: '))!.slice('data: '.length),
      );
      expect(citationsData.citations).toEqual([]);
    } finally {
      await server.close();
      await new Promise<void>((resolve) => provider.close(() => resolve()));
    }
  });
});
