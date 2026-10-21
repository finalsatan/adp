import {
  all,
  get,
  type ChunkRow,
  type Database,
  type KnowledgeBaseRow,
  type ModelRow,
} from '../db/index.js';
import { ModelError, badRequest } from '../lib/errors.js';
import { normalizeScores, termFrequency, tokenize } from '../lib/text.js';
import { safeJsonParse, type SecretBox } from '../lib/util.js';
import { addSpan } from './trace.js';
import {
  chatCompletion,
  embedTexts,
  isDemoModel,
  rerankDocuments,
  type ChatUsage,
} from './models.js';
import { openModelKey } from './modelKeys.js';

export type RetrievalMode = 'hybrid' | 'keyword' | 'vector';

export interface RetrievalConfig {
  mode: RetrievalMode;
  topK: number;
  threshold: number;
  rerankModelId?: string | null;
  rewriteModelId?: string | null;
}

export const DEFAULT_RETRIEVAL_CONFIG: RetrievalConfig = {
  mode: 'hybrid',
  topK: 5,
  threshold: 0,
  rerankModelId: null,
  rewriteModelId: null,
};

export interface RetrievalDeps {
  db: Database;
  secretBox: SecretBox;
  timeoutMs: number;
}

export interface Hit {
  id: string;
  documentId: string;
  kbId: string;
  documentName: string;
  content: string;
  headingPath: string[];
  position: number;
  enabled: boolean;
  edited: boolean;
  parentId?: string | null;
  isParent: boolean;
  sourceRef: Record<string, unknown>;
  score: number;
  keywordScore?: number;
  vectorScore?: number;
  rerankScore?: number;
}

export interface RetrievalStage {
  name: string;
  durationMs: number;
  items: Hit[];
  note?: string;
}

export interface RetrievalResult {
  hits: Hit[];
  stages: RetrievalStage[];
  rewrittenQuery: string;
  tokenCount: number;
}

interface ChunkWithDocument extends ChunkRow {
  document_name: string;
}

function modelForId(db: Database, id: string | null | undefined, kind: ModelRow['kind']): ModelRow {
  if (!id) throw badRequest(`未配置 ${kind} 模型`);
  const row = get<ModelRow>(db, 'SELECT * FROM models WHERE id = ?', id);
  if (!row) throw badRequest(`模型不存在：${id}`);
  if (row.kind !== kind) throw badRequest(`模型 ${row.name} 的类型不是 ${kind}`);
  if (row.enabled !== 1) throw badRequest(`模型 ${row.name} 已禁用`);
  return row;
}

function assertModelForKb(model: ModelRow, kbs: KnowledgeBaseRow[]): void {
  if (model.scope === 'platform') return;
  const orgIds = new Set(kbs.map((kb) => kb.org_id));
  if (!model.org_id || !orgIds.has(model.org_id)) {
    throw badRequest(`模型 ${model.name} 不属于当前知识库组织`);
  }
}

function activeChunks(db: Database, kbs: KnowledgeBaseRow[]): ChunkWithDocument[] {
  if (kbs.length === 0) return [];
  const placeholders = kbs.map(() => '?').join(',');
  return all<ChunkWithDocument>(
    db,
    `SELECT c.*, d.name AS document_name
     FROM chunks c
     JOIN documents d ON d.id = c.document_id
     WHERE c.kb_id IN (${placeholders})
       AND c.enabled = 1
       AND c.is_parent = 0
       AND (c.parent_id IS NULL OR EXISTS (
         SELECT 1 FROM chunks p WHERE p.id = c.parent_id AND p.enabled = 1
       ))`,
    ...kbs.map((kb) => kb.id),
  );
}

function toHit(row: ChunkWithDocument): Hit {
  return {
    id: row.id,
    documentId: row.document_id,
    kbId: row.kb_id,
    documentName: row.document_name,
    content: row.content,
    headingPath: safeJsonParse<string[]>(row.heading_path, []),
    position: row.position,
    enabled: row.enabled === 1,
    edited: row.edited === 1,
    parentId: row.parent_id,
    isParent: row.is_parent === 1,
    sourceRef: safeJsonParse<Record<string, unknown>>(row.source_ref, {}),
    score: 0,
  };
}

function bm25Scored(queryTokens: string[], rows: ChunkWithDocument[]): Hit[] {
  if (queryTokens.length === 0 || rows.length === 0) return [];
  const uniqueQuery = [...new Set(queryTokens)];
  const docs = rows.map((row) => {
    const tf = termFrequency(tokenize(row.content));
    let length = 0;
    for (const value of tf.values()) length += value;
    return { row, tf, length };
  });
  const avgLength = docs.reduce((sum, doc) => sum + doc.length, 0) / Math.max(1, docs.length);
  const documentFrequency = new Map<string, number>();
  for (const token of uniqueQuery) {
    let df = 0;
    for (const doc of docs) if ((doc.tf.get(token) ?? 0) > 0) df += 1;
    documentFrequency.set(token, df);
  }
  const k1 = 1.5;
  const b = 0.75;
  const scored: Hit[] = [];
  for (const doc of docs) {
    let score = 0;
    for (const token of uniqueQuery) {
      const tf = doc.tf.get(token) ?? 0;
      if (tf === 0) continue;
      const df = documentFrequency.get(token) ?? 0;
      const idf = Math.log(1 + (docs.length - df + 0.5) / (df + 0.5));
      const denominator = tf + k1 * (1 - b + (b * doc.length) / Math.max(1, avgLength));
      score += idf * ((tf * (k1 + 1)) / denominator);
    }
    if (score > 0) {
      const hit = toHit(doc.row);
      hit.keywordScore = score;
      hit.score = score;
      scored.push(hit);
    }
  }
  return scored.sort((a, b2) => b2.score - a.score);
}

async function vectorScored(
  deps: RetrievalDeps,
  rows: ChunkWithDocument[],
  query: string,
  embeddingModels: Map<string, ModelRow>,
  usage: ChatUsage,
): Promise<Hit[]> {
  if (rows.length === 0) return [];
  const byKb = new Map<string, ChunkWithDocument[]>();
  for (const row of rows) {
    const list = byKb.get(row.kb_id) ?? [];
    list.push(row);
    byKb.set(row.kb_id, list);
  }
  const hits: Hit[] = [];
  for (const [kbId, kbRows] of byKb) {
    const model = embeddingModels.get(kbId);
    if (!model) throw new ModelError('知识库未配置可用的 Embedding 模型');
    const key = openModelKey(deps.secretBox, model);
    const result = await embedTexts(model, key, [query], { timeoutMs: deps.timeoutMs });
    usage.promptTokens = (usage.promptTokens ?? 0) + (result.usage?.promptTokens ?? 0);
    usage.totalTokens = (usage.totalTokens ?? 0) + (result.usage?.totalTokens ?? 0);
    const queryVector = result.vectors[0];
    if (!queryVector) throw new ModelError('Embedding 服务未返回查询向量');
    const ids = kbRows.map((row) => row.id);
    const placeholders = ids.map(() => '?').join(',');
    const embeddings = all<{ chunk_id: string; vector: string }>(
      deps.db,
      `SELECT chunk_id, vector FROM chunk_embeddings
       WHERE model_id = ? AND chunk_id IN (${placeholders})`,
      model.id,
      ...ids,
    );
    const vectors = new Map<string, number[]>();
    for (const embedding of embeddings) {
      vectors.set(embedding.chunk_id, safeJsonParse<number[]>(embedding.vector, []));
    }
    for (const row of kbRows) {
      const vector = vectors.get(row.id);
      if (!vector || vector.length === 0) continue;
      const cosine =
        vector.reduce((sum, value, index) => sum + value * (queryVector[index] ?? 0), 0) /
        Math.max(
          1e-9,
          Math.sqrt(vector.reduce((sum, value) => sum + value * value, 0)) *
            Math.sqrt(queryVector.reduce((sum, value) => sum + value * value, 0)),
        );
      if (cosine <= 0) continue;
      const hit = toHit(row);
      hit.vectorScore = cosine;
      hit.score = cosine;
      hits.push(hit);
    }
  }
  return hits.sort((a, b) => b.score - a.score);
}

function reciprocalRankFusion(
  keyword: Hit[],
  vector: Hit[],
): Hit[] {
  const map = new Map<string, Hit>();
  const rrf = new Map<string, number>();
  keyword.forEach((hit, index) => {
    rrf.set(hit.id, (rrf.get(hit.id) ?? 0) + 1 / (60 + index + 1));
    map.set(hit.id, { ...hit });
  });
  vector.forEach((hit, index) => {
    rrf.set(hit.id, (rrf.get(hit.id) ?? 0) + 1 / (60 + index + 1));
    const existing = map.get(hit.id);
    if (existing) {
      existing.vectorScore = hit.vectorScore;
    } else {
      map.set(hit.id, { ...hit });
    }
  });
  const fused = [...map.values()].map((hit) => ({ ...hit, score: rrf.get(hit.id) ?? 0 }));
  return normalizeScores(fused).sort((a, b) => b.score - a.score);
}

function rankOrder(items: Hit[]): Hit[] {
  return items.sort((a, b) => b.score - a.score);
}

function backfillParentChunks(
  db: Database,
  hits: Hit[],
): { hits: Hit[]; applied: boolean } {
  const byParent = new Map<string, Hit>();
  let applied = false;
  for (const hit of hits) {
    if (!hit.parentId) {
      const existing = byParent.get(hit.id);
      if (!existing || hit.score > existing.score) byParent.set(hit.id, hit);
      continue;
    }
    applied = true;
    const parentRow = get<ChunkWithDocument>(
      db,
      `SELECT p.*, d.name AS document_name
       FROM chunks p JOIN documents d ON d.id = p.document_id
       WHERE p.id = ?`,
      hit.parentId,
    );
    if (!parentRow) {
      const existing = byParent.get(hit.id);
      if (!existing || hit.score > existing.score) byParent.set(hit.id, hit);
      continue;
    }
    const parent = toHit(parentRow);
    parent.score = hit.score;
    parent.keywordScore = hit.keywordScore;
    parent.vectorScore = hit.vectorScore;
    parent.rerankScore = hit.rerankScore;
    const existing = byParent.get(parent.id);
    if (!existing || parent.score > existing.score) byParent.set(parent.id, parent);
  }
  return {
    hits: [...byParent.values()].sort((a, b) => b.score - a.score),
    applied,
  };
}

export async function retrieveKnowledge(
  deps: RetrievalDeps,
  kbs: KnowledgeBaseRow[],
  rawQuery: string,
  config: RetrievalConfig,
  traceId?: string,
): Promise<RetrievalResult> {
  const query = rawQuery.trim();
  if (!query) throw badRequest('检索问题不能为空');
  if (kbs.length === 0) {
    return { hits: [], stages: [], rewrittenQuery: query, tokenCount: 0 };
  }
  const stages: RetrievalStage[] = [];
  const usage: ChatUsage = {};
  let rewrittenQuery = query;

  const recordStage = (name: string, started: number, items: Hit[], note?: string) => {
    const durationMs = Math.round(performance.now() - started);
    const stage: RetrievalStage = { name, durationMs, items, ...(note ? { note } : {}) };
    stages.push(stage);
    if (traceId) {
      addSpan(deps.db, traceId, {
        name,
        kind: 'retrieval',
        durationMs,
        input: { query: rewrittenQuery },
        output: {
          items: items.slice(0, 10).map((hit) => ({
            id: hit.id,
            documentName: hit.documentName,
            score: Number(hit.score.toFixed(5)),
            keywordScore: hit.keywordScore,
            vectorScore: hit.vectorScore,
            rerankScore: hit.rerankScore,
          })),
          note,
        },
        status: 'OK',
      });
    }
  };

  if (config.rewriteModelId) {
    const started = performance.now();
    const model = modelForId(deps.db, config.rewriteModelId, 'chat');
    assertModelForKb(model, kbs);
    if (isDemoModel(model)) {
      recordStage('rewrite', started, [], '本地演示模型不执行查询改写，已跳过（未调用模型）');
    } else {
      try {
        const result = await chatCompletion(
          model,
          openModelKey(deps.secretBox, model),
          [
            {
              role: 'system',
              content:
                '你是检索查询改写器。只输出一条更适合知识库检索的中文查询，保留原意和关键实体，不要解释，不要引号。',
            },
            { role: 'user', content: query },
          ],
          { timeoutMs: deps.timeoutMs, temperature: 0, maxTokens: 120 },
        );
        const candidate = result.text.trim().replace(/^["'“”]+|["'“”]+$/g, '').slice(0, 500);
        if (candidate) rewrittenQuery = candidate;
        usage.promptTokens = (usage.promptTokens ?? 0) + (result.usage.promptTokens ?? 0);
        usage.totalTokens = (usage.totalTokens ?? 0) + (result.usage.totalTokens ?? 0);
        recordStage('rewrite', started, [], `改写为：${rewrittenQuery}`);
      } catch (error) {
        recordStage('rewrite', started, [], error instanceof Error ? error.message : String(error));
        throw error;
      }
    }
  }

  const rows = activeChunks(deps.db, kbs);
  if (rows.length === 0) {
    return { hits: [], stages, rewrittenQuery, tokenCount: usage.totalTokens ?? 0 };
  }

  let keywordHits: Hit[] = [];
  if (config.mode === 'hybrid' || config.mode === 'keyword') {
    const started = performance.now();
    // Normalise per knowledge base before globally ranking, so corpus size does not dominate.
    const perKb: Hit[] = [];
    for (const kb of kbs) {
      const kbRows = rows.filter((row) => row.kb_id === kb.id);
      const scored = bm25Scored(tokenize(rewrittenQuery), kbRows);
      const normalized = normalizeScores(scored);
      for (const hit of normalized) {
        hit.keywordScore = hit.score;
        perKb.push(hit);
      }
    }
    keywordHits = rankOrder(perKb);
    recordStage('keyword', started, keywordHits.slice(0, Math.max(config.topK * 5, 20)));
  }

  let vectorHits: Hit[] = [];
  if (config.mode === 'hybrid' || config.mode === 'vector') {
    const started = performance.now();
    const embeddingModels = new Map<string, ModelRow>();
    for (const kb of kbs) {
      if (!kb.embedding_model_id) throw new ModelError(`知识库「${kb.name}」未配置 Embedding 模型`);
      const model = modelForId(deps.db, kb.embedding_model_id, 'embedding');
      assertModelForKb(model, kbs);
      embeddingModels.set(kb.id, model);
    }
    try {
      vectorHits = await vectorScored(deps, rows, rewrittenQuery, embeddingModels, usage);
      recordStage('vector', started, vectorHits.slice(0, Math.max(config.topK * 5, 20)));
    } catch (error) {
      recordStage('vector', started, [], error instanceof Error ? error.message : String(error));
      throw error;
    }
  }

  let fused: Hit[];
  if (config.mode === 'keyword') {
    fused = normalizeScores(keywordHits);
    for (const hit of fused) hit.keywordScore = hit.score;
  } else if (config.mode === 'vector') {
    fused = normalizeScores(vectorHits);
    for (const hit of fused) hit.vectorScore = hit.score;
  } else {
    const started = performance.now();
    fused = reciprocalRankFusion(keywordHits, vectorHits);
    recordStage('fusion', started, fused.slice(0, Math.max(config.topK * 5, 20)));
  }

  if (config.threshold > 0) {
    fused = fused.filter((hit) => hit.score >= config.threshold);
  }

  if (config.rerankModelId && fused.length > 0) {
    const started = performance.now();
    const model = modelForId(deps.db, config.rerankModelId, 'rerank');
    assertModelForKb(model, kbs);
    const candidates = fused.slice(0, Math.max(config.topK * 3, 20));
    try {
      const results = await rerankDocuments(
        model,
        openModelKey(deps.secretBox, model),
        rewrittenQuery,
        candidates.map((hit) => hit.content.slice(0, 4000)),
        { timeoutMs: deps.timeoutMs },
      );
      const byIndex = new Map<number, number>();
      for (const result of results) byIndex.set(result.index, result.score);
      const reranked = candidates.map((hit, index) => {
        const score = byIndex.get(index);
        return {
          ...hit,
          rerankScore: score,
          score: score === undefined ? 0 : Math.max(0, score),
        };
      });
      reranked.sort((a, b) => b.score - a.score);
      // Only rerank-scored candidates remain eligible. Appending the unranked
      // tail would let low-scoring chunks bypass the rerank model.
      fused = reranked;
      recordStage('rerank', started, fused.slice(0, Math.max(config.topK * 3, 20)));
    } catch (error) {
      recordStage('rerank', started, [], error instanceof Error ? error.message : String(error));
      throw error;
    }
  }

  // Threshold is applied again after rerank because the rerank score is the
  // final relevance signal for those candidates.
  if (config.rerankModelId && config.threshold > 0) {
    fused = fused.filter((hit) => hit.score >= config.threshold);
  }

  // Parent-child strategy: recall child chunks, then send the parent context to
  // the answer model. Citations therefore use the parent ID/content/location.
  const parentStarted = performance.now();
  const backfilled = backfillParentChunks(deps.db, fused);
  if (backfilled.applied) {
    fused = backfilled.hits;
    recordStage(
      'parent',
      parentStarted,
      fused.slice(0, Math.max(config.topK * 3, 20)),
      '子块召回后回填父块并去重；引用使用父块 ID、内容与定位',
    );
  }

  const hits = fused.slice(0, config.topK);
  return {
    hits,
    stages,
    rewrittenQuery,
    tokenCount: usage.totalTokens ?? 0,
  };
}
