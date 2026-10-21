import {
  all,
  get,
  run,
  type AppRow,
  type Database,
  type EvalCaseRow,
  type EvalRunRow,
  type EvalSetRow,
  type KnowledgeBaseRow,
  type ModelRow,
} from '../db/index.js';
import type { AppConfig } from '../config.js';
import { badRequest, ModelError } from '../lib/errors.js';
import { lexicalF1 } from '../lib/text.js';
import { newId, nowIso, safeJsonParse, type SecretBox } from '../lib/util.js';
import { runChat, type ChatOutput } from './chat.js';
import { chatCompletion, isDemoModel } from './models.js';
import { openModelKey } from './modelKeys.js';
import { addSpan, finishTrace, startTrace } from './trace.js';
import type { PublishedConfig } from './serializers.js';
import { DEFAULT_RETRIEVAL_CONFIG, type RetrievalConfig } from './retrieval.js';

export interface EvalDeps {
  db: Database;
  secretBox: SecretBox;
  config: AppConfig;
}

type OptionalMetric = number | null;

export interface EvalMetrics {
  total: number;
  evaluated: number;
  answerMatch: OptionalMetric;
  citationAccuracy: OptionalMetric;
  retrievalRecall: OptionalMetric;
  refusalRate: OptionalMetric;
  avgLatencyMs: OptionalMetric;
  denominators: {
    answerMatch: number;
    citationAccuracy: number;
    retrievalRecall: number;
    refusalRate: number;
    avgLatencyMs: number;
  };
  labels: Record<string, string>;
  demoProvider: boolean;
}

interface CaseScore {
  answerMatch: OptionalMetric;
  citationAccuracy: OptionalMetric;
  retrievalRecall: OptionalMetric;
  refused: boolean;
  latencyMs: number;
  expectedDocumentId: string | null;
  citationCount: number;
  retrievalCount: number;
}

function isRefusal(output: ChatOutput): boolean {
  if (output.refused) return true;
  return output.citations.length === 0 && /无法(确认|回答)|资料不足|未找到|没有找到/.test(output.answer);
}

function scoreCase(
  output: ChatOutput,
  latencyMs: number,
  expectedAnswer: string,
  expectedDocumentId: string | null,
): CaseScore {
  const refused = isRefusal(output);
  const answerMatch = expectedAnswer.trim() ? lexicalF1(output.answer, expectedAnswer) : null;
  let citationAccuracy: number | null = null;
  if (expectedDocumentId) {
    if (output.citations.length === 0) {
      citationAccuracy = 0;
    } else {
      citationAccuracy =
        output.citations.filter((citation) => citation.documentId === expectedDocumentId).length /
        output.citations.length;
    }
  }
  const retrievalRecall = expectedDocumentId
    ? output.hits.some((hit) => hit.documentId === expectedDocumentId)
      ? 1
      : 0
    : null;
  return {
    answerMatch,
    citationAccuracy,
    retrievalRecall,
    refused,
    latencyMs,
    expectedDocumentId,
    citationCount: output.citations.length,
    retrievalCount: output.hits.length,
  };
}

function aggregate(scores: CaseScore[], demoProvider: boolean, total: number): EvalMetrics {
  const values = (selector: (score: CaseScore) => number | null): number[] =>
    scores.map(selector).filter((value): value is number => value !== null);
  const avg = (items: number[]): number | null =>
    items.length === 0 ? null : items.reduce((sum, value) => sum + value, 0) / items.length;
  const answerValues = values((score) => score.answerMatch);
  const citationValues = values((score) => score.citationAccuracy);
  const recallValues = values((score) => score.retrievalRecall);
  const refusalValues = scores.map((score) => (score.refused ? 1 : 0));
  const latencyValues = scores.map((score) => score.latencyMs);
  return {
    total,
    evaluated: scores.length,
    answerMatch: avg(answerValues),
    citationAccuracy: avg(citationValues),
    retrievalRecall: avg(recallValues),
    refusalRate: avg(refusalValues),
    avgLatencyMs: avg(latencyValues),
    denominators: {
      answerMatch: answerValues.length,
      citationAccuracy: citationValues.length,
      retrievalRecall: recallValues.length,
      refusalRate: refusalValues.length,
      avgLatencyMs: latencyValues.length,
    },
    labels: {
      answerMatch: '词项匹配参考值（非业务正确率，需业务确认）',
      citationAccuracy: '仅统计标注了目标文档的用例：引用中目标文档占比',
      retrievalRecall: '仅统计标注了目标文档的用例：目标文档是否进入 TopK 召回',
      refusalRate: '无依据拒答或未给出引用的比例',
      avgLatencyMs: '单用例检索加回答平均耗时',
    },
    demoProvider,
  };
}

export class EvalService {
  constructor(private readonly deps: EvalDeps) {}

  async run(runId: string, setProgress?: (progress: number) => void): Promise<void> {
    const { db, secretBox, config } = this.deps;
    const runRow = get<EvalRunRow>(db, 'SELECT * FROM eval_runs WHERE id = ?', runId);
    if (!runRow) throw new Error('评测运行不存在');
    const set = get<EvalSetRow>(db, 'SELECT * FROM eval_sets WHERE id = ?', runRow.set_id);
    if (!set) throw new Error('评测集不存在');
    const app = get<AppRow>(db, 'SELECT * FROM apps WHERE id = ?', set.app_id);
    if (!app) throw new Error('评测应用不存在');
    const snapshot = safeJsonParse<PublishedConfig>(runRow.app_snapshot, {
      name: app.name,
      description: app.description,
      visibility: app.visibility,
      orgId: app.org_id,
      ownerId: app.owner_id,
      kbIds: safeJsonParse(app.kb_ids, []),
      chatModelId: app.chat_model_id,
      systemPrompt: app.system_prompt,
      retrievalConfig: safeJsonParse<RetrievalConfig>(runRow.config_a, { ...DEFAULT_RETRIEVAL_CONFIG }),
    });
    const kbs = all<KnowledgeBaseRow>(db, 'SELECT * FROM knowledge_bases WHERE id IN (' + snapshot.kbIds.map(() => '?').join(',') + ')', ...snapshot.kbIds);
    if (kbs.length !== snapshot.kbIds.length) throw new Error('评测快照中的知识库已被删除');
    const chatModel = get<ModelRow>(db, 'SELECT * FROM models WHERE id = ?', snapshot.chatModelId);
    if (!chatModel) throw new Error('评测快照中的对话模型已被删除');
    interface CaseSnapshot {
      id: string;
      question: string;
      expectedAnswer: string;
      documentId: string | null;
    }
    const frozenCases = safeJsonParse<
      Array<{ id?: string; question?: string; expectedAnswer?: string; documentId?: string | null }>
    >(runRow.case_snapshot, []);
    const cases: CaseSnapshot[] =
      frozenCases.length > 0
        ? frozenCases
            .filter((item) => typeof item.id === 'string' && typeof item.question === 'string')
            .map((item) => ({
              id: item.id!,
              question: item.question!,
              expectedAnswer: item.expectedAnswer ?? '',
              documentId: item.documentId ?? null,
            }))
        : all<EvalCaseRow>(db, 'SELECT * FROM eval_cases WHERE set_id = ? ORDER BY created_at ASC', set.id).map(
            (item) => ({
              id: item.id,
              question: item.question,
              expectedAnswer: item.expected_answer,
              documentId: item.document_id,
            }),
          );
    const configA = safeJsonParse<RetrievalConfig>(runRow.config_a, { ...DEFAULT_RETRIEVAL_CONFIG });
    const configB = runRow.config_b
      ? safeJsonParse<RetrievalConfig>(runRow.config_b, configA)
      : undefined;
    const trace = startTrace(db, {
      kind: 'eval',
      name: `评测运行：${runRow.name}`,
      orgId: set.org_id,
      appId: app.id,
      input: { runId, setId: set.id, cases: cases.length, configA, configB, demoProvider: isDemoModel(chatModel) },
    });

    // A retried job must rebuild all results from scratch. The unique
    // (run_id, case_id) index plus this delete keeps retries idempotent.
    run(db, 'DELETE FROM eval_results WHERE run_id = ?', runId);
    run(
      db,
      "UPDATE eval_runs SET status = 'RUNNING', progress = 0, error = NULL, started_at = ?, finished_at = NULL WHERE id = ?",
      nowIso(),
      runId,
    );
    const scoresA: CaseScore[] = [];
    const scoresB: CaseScore[] = [];
    let traceTokens = 0;

    try {
      for (let index = 0; index < cases.length; index += 1) {
        const testCase = cases[index]!;
        const started = performance.now();
        const outputA = await runChat(
          {
            kbs,
            chatModel,
            systemPrompt: snapshot.systemPrompt,
            question: testCase.question,
            retrievalConfig: configA,
            traceId: trace.id,
            appId: app.id,
          },
          { db, secretBox, config },
        );
        const latencyA = Math.round(performance.now() - started);
        traceTokens += outputA.tokenCount;
        const scoreA = scoreCase(outputA, latencyA, testCase.expectedAnswer, testCase.documentId);
        scoresA.push(scoreA);
        let outputB: ChatOutput | undefined;
        let scoreB: CaseScore | undefined;
        let latencyB: number | undefined;
        if (configB) {
          const startedB = performance.now();
          outputB = await runChat(
            {
              kbs,
              chatModel,
              systemPrompt: snapshot.systemPrompt,
              question: testCase.question,
              retrievalConfig: configB,
              traceId: trace.id,
              appId: app.id,
            },
            { db, secretBox, config },
          );
          latencyB = Math.round(performance.now() - startedB);
          traceTokens += outputB.tokenCount;
          scoreB = scoreCase(outputB, latencyB, testCase.expectedAnswer, testCase.documentId);
          scoresB.push(scoreB);
        }
        run(
          db,
          `INSERT INTO eval_results
           (id, run_id, case_id, question, expected_answer, answer_a, answer_b, citations_a, citations_b,
            scores_a, scores_b, latency_ms, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(run_id, case_id) DO UPDATE SET
             question = excluded.question,
             expected_answer = excluded.expected_answer,
             answer_a = excluded.answer_a,
             answer_b = excluded.answer_b,
             citations_a = excluded.citations_a,
             citations_b = excluded.citations_b,
             scores_a = excluded.scores_a,
             scores_b = excluded.scores_b,
             latency_ms = excluded.latency_ms,
             created_at = excluded.created_at`,
          newId(),
          runId,
          testCase.id,
          testCase.question,
          testCase.expectedAnswer,
          outputA.answer,
          outputB?.answer ?? null,
          JSON.stringify(outputA.citations),
          outputB ? JSON.stringify(outputB.citations) : '[]',
          JSON.stringify(scoreA),
          scoreB ? JSON.stringify(scoreB) : null,
          latencyA,
          nowIso(),
        );
        addSpan(db, trace.id, {
          name: `case ${index + 1}`,
          kind: 'eval-case',
          durationMs: latencyA + (latencyB ?? 0),
          input: {
            question: testCase.question,
            expectedDocumentId: testCase.documentId,
            expectedAnswer: testCase.expectedAnswer.slice(0, 500),
          },
          output: {
            scoresA: scoreA,
            scoresB: scoreB,
            demoProvider: isDemoModel(chatModel),
          },
          status: 'OK',
        });
        const progress = Math.round(((index + 1) / Math.max(1, cases.length)) * 100);
        run(db, 'UPDATE eval_runs SET progress = ? WHERE id = ?', progress, runId);
        setProgress?.(progress);
      }

      const metricsA = aggregate(scoresA, isDemoModel(chatModel), cases.length);
      const metricsB = configB ? aggregate(scoresB, isDemoModel(chatModel), cases.length) : null;
      run(
        db,
        `UPDATE eval_runs SET status = 'COMPLETED', progress = 100, metrics_a = ?, metrics_b = ?,
           finished_at = ?, error = NULL WHERE id = ?`,
        JSON.stringify(metricsA),
        metricsB ? JSON.stringify(metricsB) : null,
        nowIso(),
        runId,
      );
      finishTrace(db, trace, {
        status: 'OK',
        tokenCount: traceTokens || undefined,
        output: { metricsA, metricsB, cases: cases.length },
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      // Keep the run in RUNNING when the job will retry. The job worker marks
      // FAILED only after the final attempt, so the UI never sees a transient
      // terminal state.
      run(db, "UPDATE eval_runs SET error = ? WHERE id = ? AND status != 'COMPLETED'", message.slice(0, 4000), runId);
      finishTrace(db, trace, { status: 'FAILED', output: { error: message } });
      throw error;
    }
  }

  async generateCases(
    payload: { setId: string; kbId: string; count: number },
    setProgress?: (progress: number) => void,
  ): Promise<void> {
    const { db, secretBox, config } = this.deps;
    const set = get<EvalSetRow>(db, 'SELECT * FROM eval_sets WHERE id = ?', payload.setId);
    if (!set) throw new Error('评测集不存在');
    const app = get<AppRow>(db, 'SELECT * FROM apps WHERE id = ?', set.app_id);
    if (!app) throw new Error('评测应用不存在');
    const kb = get<KnowledgeBaseRow>(db, 'SELECT * FROM knowledge_bases WHERE id = ?', payload.kbId);
    if (!kb) throw new Error('知识库不存在');
    const model = get<ModelRow>(db, 'SELECT * FROM models WHERE id = ?', app.chat_model_id);
    if (!model) throw new Error('应用对话模型不存在');
    if (isDemoModel(model)) {
      throw new ModelError('本地演示模型不支持按文档生成评测用例；请配置真实 Chat 模型');
    }
    const rows = all<{ id: string; document_id: string; content: string; name: string }>(
      db,
      `SELECT c.id, c.document_id, c.content, d.name
       FROM chunks c JOIN documents d ON d.id = c.document_id
       WHERE c.kb_id = ? AND c.enabled = 1 AND c.is_parent = 0
       ORDER BY c.document_id, c.position LIMIT 20`,
      kb.id,
    );
    if (rows.length === 0) throw new Error('知识库没有可用分块，无法生成评测用例');
    const excerpts: string[] = [];
    let used = 0;
    for (const row of rows) {
      const block = `文档ID：${row.document_id}\n文档名：${row.name}\n${row.content}`;
      if (used + block.length > 10_000) break;
      excerpts.push(block);
      used += block.length;
    }
    const trace = startTrace(db, {
      kind: 'eval',
      name: `生成评测用例：${set.name}`,
      orgId: set.org_id,
      appId: app.id,
      input: { setId: set.id, kbId: kb.id, count: payload.count },
    });
    const started = performance.now();
    setProgress?.(20);
    try {
      const result = await chatCompletion(
        model,
        openModelKey(secretBox, model),
        [
          {
            role: 'system',
            content:
              '你是企业知识库评测用例生成器。根据给定的文档片段生成问题与标准答案。只能使用片段中的事实，问题必须可由片段回答。documentId 必须原样使用资料中给出的“文档ID”。只输出 JSON 对象，格式为 {"cases":[{"question":"...","expectedAnswer":"...","documentId":"..."}]}，不要 Markdown。',
          },
          {
            role: 'user',
            content: `请生成 ${payload.count} 条中文评测用例。\n\n资料：\n${excerpts.join('\n\n---\n\n')}`,
          },
        ],
        { timeoutMs: config.modelTimeoutMs, temperature: 0.1, maxTokens: 2500, jsonMode: true },
      );
      setProgress?.(70);
      const parsed = parseGeneratedCases(result.text, payload.count);
      const documentIds = new Set(rows.map((row) => row.document_id));
      let inserted = 0;
      for (const item of parsed) {
        if (!item.question.trim() || !item.expectedAnswer.trim()) continue;
        const documentId = item.documentId && documentIds.has(item.documentId) ? item.documentId : null;
        run(
          db,
          `INSERT INTO eval_cases (id, set_id, question, expected_answer, document_id, tags, source, created_at)
           VALUES (?, ?, ?, ?, ?, '[]', 'generated', ?)`,
          newId(),
          set.id,
          item.question.trim().slice(0, 1000),
          item.expectedAnswer.trim().slice(0, 5000),
          documentId,
          nowIso(),
        );
        inserted += 1;
      }
      if (inserted === 0) throw new Error('模型没有返回可用的评测用例');
      addSpan(db, trace.id, {
        name: 'generate',
        kind: 'model',
        durationMs: performance.now() - started,
        input: { model: model.name, count: payload.count },
        output: { inserted, usage: result.usage },
        status: 'OK',
      });
      finishTrace(db, trace, { status: 'OK', tokenCount: result.usage.totalTokens, output: { inserted } });
      setProgress?.(100);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      finishTrace(db, trace, { status: 'FAILED', output: { error: message } });
      throw error;
    }
  }
}

function parseGeneratedCases(text: string, count: number): Array<{
  question: string;
  expectedAnswer: string;
  documentId?: string;
}> {
  const trimmed = text.trim().replace(/^```(?:json)?/i, '').replace(/```$/, '').trim();
  let parsed: unknown;
  try {
    parsed = JSON.parse(trimmed);
  } catch {
    const start = trimmed.indexOf('{');
    const end = trimmed.lastIndexOf('}');
    if (start >= 0 && end > start) {
      try {
        parsed = JSON.parse(trimmed.slice(start, end + 1));
      } catch {
        parsed = undefined;
      }
    }
  }
  const list = Array.isArray(parsed)
    ? parsed
    : parsed && typeof parsed === 'object' && Array.isArray((parsed as { cases?: unknown }).cases)
      ? ((parsed as { cases: unknown[] }).cases)
      : undefined;
  if (!list) throw new Error('模型返回的评测用例不是有效 JSON');
  return list
    .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === 'object')
    .slice(0, Math.max(1, count))
    .map((item) => ({
      question: typeof item.question === 'string' ? item.question : '',
      expectedAnswer:
        typeof item.expectedAnswer === 'string'
          ? item.expectedAnswer
          : typeof item.expected_answer === 'string'
            ? item.expected_answer
            : '',
      documentId: typeof item.documentId === 'string' ? item.documentId : undefined,
    }));
}

export function buildEvalMetricsForTests(scores: CaseScore[], demoProvider: boolean, total: number): EvalMetrics {
  return aggregate(scores, demoProvider, total);
}

export function createEvalService(deps: EvalDeps): EvalService {
  return new EvalService(deps);
}
