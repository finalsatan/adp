import crypto from 'node:crypto';
import type { ModelRow } from '../db/index.js';
import { ModelError } from '../lib/errors.js';
import { clamp, redactSecrets } from '../lib/util.js';

export interface ChatMessage {
  role: 'system' | 'user' | 'assistant';
  content: string;
}

export interface ChatUsage {
  promptTokens?: number;
  completionTokens?: number;
  totalTokens?: number;
}

export interface ChatResult {
  text: string;
  usage: ChatUsage;
  raw?: unknown;
}

export interface ChatRequestOptions {
  timeoutMs: number;
  temperature?: number;
  maxTokens?: number;
  signal?: AbortSignal;
}

export interface RerankResult {
  index: number;
  score: number;
}

export function isDemoModel(model: ModelRow): boolean {
  return model.provider === 'demo';
}

function endpoint(baseUrl: string, suffix: string): string {
  const base = (baseUrl || '').replace(/\/+$/, '');
  if (!base) throw new ModelError('模型未配置 baseUrl');
  if (base.endsWith(suffix)) return base;
  return `${base}${suffix}`;
}

function authHeaders(apiKey: string | undefined): Record<string, string> {
  const headers: Record<string, string> = { 'content-type': 'application/json' };
  if (apiKey) headers.authorization = `Bearer ${apiKey}`;
  return headers;
}

async function readErrorResponse(response: Response, apiKey?: string): Promise<string> {
  try {
    const text = await response.text();
    return redactSecrets(text.slice(0, 500) || response.statusText, [apiKey]);
  } catch {
    return response.statusText;
  }
}

function makeSignal(options: { timeoutMs: number; signal?: AbortSignal }): AbortSignal {
  const timeout = AbortSignal.timeout(options.timeoutMs);
  return options.signal ? AbortSignal.any([options.signal, timeout]) : timeout;
}

function num(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : 0;
}

/** Deterministic local lexical embedding used only by the clearly labelled demo model. */
export function demoEmbedding(text: string, dimensions = 256): number[] {
  const vector = new Array<number>(dimensions).fill(0);
  const lower = text.toLowerCase();
  const tokens: string[] = [];
  for (const word of lower.match(/[a-z0-9_]+/g) ?? []) tokens.push(`w:${word}`);
  const chinese = [...lower].filter((ch) => /[\u3400-\u9fff]/.test(ch));
  for (const ch of chinese) tokens.push(`c:${ch}`);
  for (let i = 0; i < chinese.length - 1; i += 1) tokens.push(`b:${chinese[i]}${chinese[i + 1]}`);
  if (tokens.length === 0) tokens.push(`raw:${lower.slice(0, 200)}`);

  for (const token of tokens) {
    const digest = crypto.createHash('sha256').update(token).digest();
    const index = digest.readUInt32BE(0) % dimensions;
    const sign = digest[4]! % 2 === 0 ? 1 : -1;
    vector[index] = (vector[index] ?? 0) + sign;
  }
  const norm = Math.sqrt(vector.reduce((sum, v) => sum + v * v, 0)) || 1;
  return vector.map((v) => v / norm);
}

export async function embedTexts(
  model: ModelRow,
  apiKey: string | undefined,
  texts: string[],
  options: { timeoutMs: number; signal?: AbortSignal },
): Promise<{ vectors: number[][]; dimensions: number; usage?: ChatUsage; demo: boolean }> {
  if (texts.length === 0) return { vectors: [], dimensions: model.dimensions ?? 0, demo: isDemoModel(model) };
  if (isDemoModel(model)) {
    const dims = model.dimensions ?? 256;
    return { vectors: texts.map((text) => demoEmbedding(text, dims)), dimensions: dims, demo: true };
  }
  const vectors: number[][] = [];
  let usage: ChatUsage | undefined;
  for (let offset = 0; offset < texts.length; offset += 32) {
    const batch = texts.slice(offset, offset + 32);
    let response: Response;
    try {
      response = await fetch(endpoint(model.base_url, '/embeddings'), {
        method: 'POST',
        headers: authHeaders(apiKey),
        body: JSON.stringify({ model: model.model, input: batch }),
        signal: makeSignal(options),
      });
    } catch (error) {
      throw new ModelError(redactSecrets(`Embedding 模型调用失败：${error instanceof Error ? error.message : String(error)}`, [apiKey]));
    }
    if (!response.ok) {
      throw new ModelError(`Embedding 模型返回 ${response.status}：${await readErrorResponse(response, apiKey)}`);
    }
    const payload = (await response.json()) as {
      data?: Array<{ embedding?: unknown; index?: number }>;
      usage?: { prompt_tokens?: number; total_tokens?: number };
    };
    if (!Array.isArray(payload.data) || payload.data.length !== batch.length) {
      throw new ModelError('Embedding 响应格式无效或数量不匹配');
    }
    const ordered = [...payload.data].sort((a, b) => num(a.index) - num(b.index));
    for (const item of ordered) {
      if (!Array.isArray(item.embedding) || item.embedding.some((value) => typeof value !== 'number')) {
        throw new ModelError('Embedding 响应中的向量格式无效');
      }
      const vector = item.embedding as number[];
      if (model.dimensions && vector.length !== model.dimensions) {
        throw new ModelError(`Embedding 维度不匹配：期望 ${model.dimensions}，实际 ${vector.length}`);
      }
      vectors.push(vector);
    }
    if (payload.usage) {
      usage = {
        promptTokens: (usage?.promptTokens ?? 0) + num(payload.usage.prompt_tokens),
        totalTokens: (usage?.totalTokens ?? 0) + num(payload.usage.total_tokens),
      };
    }
  }
  const dimensions = vectors[0]?.length ?? model.dimensions ?? 0;
  return { vectors, dimensions, usage, demo: false };
}

export async function chatCompletion(
  model: ModelRow,
  apiKey: string | undefined,
  messages: ChatMessage[],
  options: ChatRequestOptions & { jsonMode?: boolean },
): Promise<ChatResult> {
  if (isDemoModel(model)) {
    throw new ModelError('本地演示模型不执行大模型生成；知识问答将使用明确的检索摘录回答');
  }
  let response: Response;
  try {
    response = await fetch(endpoint(model.base_url, '/chat/completions'), {
      method: 'POST',
      headers: authHeaders(apiKey),
      body: JSON.stringify({
        model: model.model,
        messages,
        temperature: options.temperature ?? 0.2,
        max_tokens: options.maxTokens,
        ...(options.jsonMode ? { response_format: { type: 'json_object' } } : {}),
      }),
      signal: makeSignal(options),
    });
  } catch (error) {
    throw new ModelError(redactSecrets(`Chat 模型调用失败：${error instanceof Error ? error.message : String(error)}`, [apiKey]));
  }
  if (!response.ok) {
    throw new ModelError(`Chat 模型返回 ${response.status}：${await readErrorResponse(response, apiKey)}`);
  }
  const payload = (await response.json()) as {
    choices?: Array<{ message?: { content?: string | null } }>;
    usage?: { prompt_tokens?: number; completion_tokens?: number; total_tokens?: number };
  };
  const text = payload.choices?.[0]?.message?.content;
  if (typeof text !== 'string') throw new ModelError('Chat 模型响应中没有文本内容');
  return {
    text,
    usage: {
      promptTokens: payload.usage?.prompt_tokens,
      completionTokens: payload.usage?.completion_tokens,
      totalTokens: payload.usage?.total_tokens,
    },
    raw: payload,
  };
}

export async function chatCompletionStream(
  model: ModelRow,
  apiKey: string | undefined,
  messages: ChatMessage[],
  options: ChatRequestOptions & { onDelta: (text: string) => void | Promise<void> },
): Promise<ChatResult> {
  if (isDemoModel(model)) {
    throw new ModelError('本地演示模型不支持流式生成');
  }
  let response: Response;
  try {
    response = await fetch(endpoint(model.base_url, '/chat/completions'), {
      method: 'POST',
      headers: authHeaders(apiKey),
      body: JSON.stringify({
        model: model.model,
        messages,
        temperature: options.temperature ?? 0.2,
        max_tokens: options.maxTokens,
        stream: true,
        stream_options: { include_usage: true },
      }),
      signal: makeSignal(options),
    });
  } catch (error) {
    throw new ModelError(redactSecrets(`Chat 流式调用失败：${error instanceof Error ? error.message : String(error)}`, [apiKey]));
  }
  if (!response.ok) {
    throw new ModelError(`Chat 模型返回 ${response.status}：${await readErrorResponse(response, apiKey)}`);
  }
  if (!response.body) throw new ModelError('Chat 模型没有返回流式响应体');
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let text = '';
  let usage: ChatUsage = {};
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop() ?? '';
    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed.startsWith('data:')) continue;
      const data = trimmed.slice(5).trim();
      if (!data || data === '[DONE]') continue;
      let parsed: {
        choices?: Array<{ delta?: { content?: string | null } }>;
        usage?: { prompt_tokens?: number; completion_tokens?: number; total_tokens?: number };
      };
      try {
        parsed = JSON.parse(data) as typeof parsed;
      } catch {
        continue;
      }
      const delta = parsed.choices?.[0]?.delta?.content;
      if (typeof delta === 'string' && delta.length > 0) {
        text += delta;
        await options.onDelta(delta);
      }
      if (parsed.usage) {
        usage = {
          promptTokens: parsed.usage.prompt_tokens,
          completionTokens: parsed.usage.completion_tokens,
          totalTokens: parsed.usage.total_tokens,
        };
      }
    }
  }
  return { text, usage };
}

export async function rerankDocuments(
  model: ModelRow,
  apiKey: string | undefined,
  query: string,
  documents: string[],
  options: { timeoutMs: number; signal?: AbortSignal },
): Promise<RerankResult[]> {
  if (isDemoModel(model)) {
    throw new ModelError('本地演示模型不提供重排能力；请配置真实的 rerank 模型');
  }
  let response: Response;
  try {
    response = await fetch(endpoint(model.base_url, '/rerank'), {
      method: 'POST',
      headers: authHeaders(apiKey),
      body: JSON.stringify({ model: model.model, query, documents, top_n: documents.length }),
      signal: makeSignal(options),
    });
  } catch (error) {
    throw new ModelError(redactSecrets(`Rerank 模型调用失败：${error instanceof Error ? error.message : String(error)}`, [apiKey]));
  }
  if (!response.ok) {
    throw new ModelError(`Rerank 模型返回 ${response.status}：${await readErrorResponse(response, apiKey)}`);
  }
  const payload = (await response.json()) as {
    results?: Array<{ index?: unknown; relevance_score?: unknown; score?: unknown }>;
    data?: Array<{ index?: unknown; relevance_score?: unknown; score?: unknown }>;
  };
  const raw = payload.results ?? payload.data;
  if (!Array.isArray(raw)) throw new ModelError('Rerank 响应格式无效');
  return raw
    .map((item) => {
      const index = Math.trunc(num(item.index));
      const score = num(item.relevance_score ?? item.score);
      return { index, score: clamp(score, -1, 1) };
    })
    .filter((item) => item.index >= 0 && item.index < documents.length)
    .sort((a, b) => b.score - a.score);
}

export async function testModel(
  model: ModelRow,
  apiKey: string | undefined,
  options: { timeoutMs: number },
): Promise<{ ok: boolean; message: string; latencyMs: number }> {
  const started = performance.now();
  try {
    if (isDemoModel(model)) {
      if (model.kind === 'rerank') {
        return {
          ok: false,
          message: '本地演示模型不提供重排能力，请配置真实 rerank 模型',
          latencyMs: Math.round(performance.now() - started),
        };
      }
      if (model.kind === 'embedding') {
        const result = await embedTexts(model, undefined, ['本地演示向量测试'], options);
        return {
          ok: true,
          message: `本地演示 Embedding 可用（${result.dimensions} 维，词面哈希向量，不代表语义质量）`,
          latencyMs: Math.round(performance.now() - started),
        };
      }
      return {
        ok: true,
        message: '本地演示 Chat 可用：问答时只做检索摘录，不调用大模型',
        latencyMs: Math.round(performance.now() - started),
      };
    }
    if (model.kind === 'embedding') {
      const result = await embedTexts(model, apiKey, ['ping'], options);
      return {
        ok: true,
        message: `Embedding 调用成功（${result.dimensions} 维）`,
        latencyMs: Math.round(performance.now() - started),
      };
    }
    if (model.kind === 'rerank') {
      const results = await rerankDocuments(model, apiKey, 'ping', ['ping', 'pong'], options);
      return {
        ok: true,
        message: `Rerank 调用成功（返回 ${results.length} 条）`,
        latencyMs: Math.round(performance.now() - started),
      };
    }
    const result = await chatCompletion(
      model,
      apiKey,
      [{ role: 'user', content: '请回复“pong”' }],
      { ...options, maxTokens: 8 },
    );
    return {
      ok: true,
      message: `Chat 调用成功：${result.text.slice(0, 120)}`,
      latencyMs: Math.round(performance.now() - started),
    };
  } catch (error) {
    return {
      ok: false,
      message: redactSecrets(error instanceof Error ? error.message : String(error), [apiKey]),
      latencyMs: Math.round(performance.now() - started),
    };
  }
}
