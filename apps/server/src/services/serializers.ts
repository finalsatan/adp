import {
  all,
  get,
  type AppRow,
  type AppVersionRow,
  type ChunkRow,
  type ConversationRow,
  type Database,
  type DocumentRow,
  type EvalCaseRow,
  type EvalRunRow,
  type EvalSetRow,
  type EvalReviewRow,
  type FeedbackRow,
  type KnowledgeBaseRow,
  type MessageRow,
  type UserRow,
} from '../db/index.js';
import { safeJsonParse } from '../lib/util.js';
import type { ChunkConfig } from '../services/chunker.js';
import type { RetrievalConfig } from '../services/retrieval.js';
import type { KnowledgeBaseAccess, AppAccess } from '../services/access.js';

export function serializeKb(
  db: Database,
  kb: KnowledgeBaseRow,
  access?: KnowledgeBaseAccess,
) {
  const documentCount = get<{ n: number }>(db, 'SELECT COUNT(*) AS n FROM documents WHERE kb_id = ?', kb.id)?.n ?? 0;
  const chunkCount = get<{ n: number }>(db, 'SELECT COUNT(*) AS n FROM chunks WHERE kb_id = ?', kb.id)?.n ?? 0;
  return {
    id: kb.id,
    name: kb.name,
    description: kb.description,
    visibility: kb.visibility,
    orgId: kb.org_id,
    ownerId: kb.owner_id,
    embeddingModelId: kb.embedding_model_id,
    documentCount,
    chunkCount,
    chunkConfig: safeJsonParse<ChunkConfig>(kb.chunk_config, {
      strategy: 'structure',
      maxChars: 800,
      overlap: 100,
    }),
    retrievalConfig: safeJsonParse<RetrievalConfig>(kb.retrieval_config, {
      mode: 'hybrid',
      topK: 5,
      threshold: 0,
    }),
    createdAt: kb.created_at,
    ...(access ? { access } : {}),
  };
}

export function serializeDocument(row: DocumentRow, includeParsedText = false) {
  return {
    id: row.id,
    kbId: row.kb_id,
    name: row.name,
    mime: row.mime,
    size: row.size,
    status: row.status,
    progress: row.progress,
    error: row.error,
    chunkCount: row.chunk_count,
    version: row.version,
    createdAt: row.created_at,
    updatedAt: row.updated_at,
    ...(includeParsedText ? { parsedText: row.parsed_text ?? '' } : {}),
  };
}

export function serializeChunk(row: ChunkRow) {
  return {
    id: row.id,
    documentId: row.document_id,
    kbId: row.kb_id,
    content: row.content,
    headingPath: safeJsonParse<string[]>(row.heading_path, []),
    position: row.position,
    enabled: row.enabled === 1,
    edited: row.edited === 1,
    parentId: row.parent_id,
    isParent: row.is_parent === 1,
    sourceRef: safeJsonParse<Record<string, unknown>>(row.source_ref, {}),
  };
}

export function serializeCitation(hit: {
  id: string;
  documentId: string;
  documentName: string;
  content: string;
  sourceRef: Record<string, unknown>;
  headingPath: string[];
}, index: number) {
  return {
    index,
    chunkId: hit.id,
    documentId: hit.documentId,
    documentName: hit.documentName,
    content: hit.content,
    sourceRef: hit.sourceRef,
    headingPath: hit.headingPath,
  };
}

export function serializeApp(db: Database, app: AppRow, access?: AppAccess) {
  return {
    id: app.id,
    name: app.name,
    description: app.description,
    visibility: app.visibility,
    orgId: app.org_id,
    ownerId: app.owner_id,
    kbIds: safeJsonParse<string[]>(app.kb_ids, []),
    chatModelId: app.chat_model_id,
    systemPrompt: app.system_prompt,
    retrievalConfig: safeJsonParse<RetrievalConfig>(app.retrieval_config, {
      mode: 'hybrid',
      topK: 5,
      threshold: 0,
    }),
    status: app.status,
    publishedVersion: app.published_version ?? undefined,
    createdAt: app.created_at,
    ...(access ? { access } : {}),
  };
}

export interface PublishedConfig {
  name: string;
  description: string;
  visibility: 'private' | 'organization' | 'platform';
  orgId: string;
  ownerId: string;
  kbIds: string[];
  chatModelId: string;
  systemPrompt: string;
  retrievalConfig: RetrievalConfig;
}

export function parseVersionConfig(row: AppVersionRow): PublishedConfig {
  return safeJsonParse<PublishedConfig>(row.config, {
    name: '',
    description: '',
    visibility: 'private',
    orgId: '',
    ownerId: '',
    kbIds: [],
    chatModelId: '',
    systemPrompt: '',
    retrievalConfig: { mode: 'hybrid', topK: 5, threshold: 0 },
  });
}

export function serializeConversation(row: ConversationRow) {
  return {
    id: row.id,
    appId: row.app_id,
    title: row.title,
    createdAt: row.created_at,
    updatedAt: row.updated_at,
  };
}

export function serializeMessage(db: Database, row: MessageRow, viewerId?: string) {
  const feedback = viewerId
    ? get<FeedbackRow>(db, 'SELECT * FROM feedback WHERE message_id = ? AND user_id = ?', row.id, viewerId)
    : undefined;
  return {
    id: row.id,
    conversationId: row.conversation_id,
    role: row.role,
    content: row.content,
    citations: safeJsonParse(row.citations, []),
    traceId: row.trace_id ?? undefined,
    createdAt: row.created_at,
    ...(feedback
      ? {
          feedback: {
            rating: feedback.rating as 1 | -1,
            correction: feedback.correction ?? undefined,
          },
        }
      : {}),
  };
}

export function serializeUser(row: UserRow, memberships?: unknown[]) {
  return {
    id: row.id,
    name: row.name,
    email: row.email,
    isSystemAdmin: row.is_system_admin === 1,
    createdAt: row.created_at,
    ...(memberships ? { memberships } : {}),
  };
}

export function membershipsForUser(db: Database, userId: string) {
  return all<{ org_id: string; role_id: string; role_name: string; org_name: string; permissions: string }>(
    db,
    `SELECT m.org_id, m.role_id, r.name AS role_name, o.name AS org_name, r.permissions
     FROM memberships m JOIN roles r ON r.id = m.role_id JOIN organizations o ON o.id = m.org_id
     WHERE m.user_id = ? ORDER BY o.created_at`,
    userId,
  ).map((row) => ({
    orgId: row.org_id,
    roleId: row.role_id,
    roleName: row.role_name,
    orgName: row.org_name,
    permissions: safeJsonParse<string[]>(row.permissions, []),
  }));
}

export function serializeRole(row: { id: string; name: string; permissions: string; is_global: number; org_id: string | null }) {
  return {
    id: row.id,
    name: row.name,
    permissions: safeJsonParse<string[]>(row.permissions, []),
    global: row.is_global === 1,
    orgId: row.org_id,
  };
}

export function serializeFeedback(db: Database, row: FeedbackRow) {
  const message = get<MessageRow>(db, 'SELECT * FROM messages WHERE id = ?', row.message_id);
  const conversation = message
    ? get<ConversationRow>(db, 'SELECT * FROM conversations WHERE id = ?', message.conversation_id)
    : undefined;
  const question = conversation
    ? get<MessageRow>(
        db,
        "SELECT * FROM messages WHERE conversation_id = ? AND role = 'user' AND created_at <= ? ORDER BY created_at DESC LIMIT 1",
        conversation.id,
        message?.created_at ?? row.created_at,
      )
    : undefined;
  return {
    id: row.id,
    messageId: row.message_id,
    question: question?.content ?? '',
    answer: message?.content ?? '',
    rating: row.rating as 1 | -1,
    correction: row.correction ?? undefined,
    appId: conversation?.app_id ?? null,
    conversationId: conversation?.id ?? null,
    addedToEval: row.added_to_eval === 1,
    createdAt: row.created_at,
  };
}

export function serializeEvalSetWithCount(db: Database, row: EvalSetRow) {
  return {
    id: row.id,
    name: row.name,
    description: row.description,
    appId: row.app_id,
    orgId: row.org_id,
    caseCount: get<{ n: number }>(db, 'SELECT COUNT(*) AS n FROM eval_cases WHERE set_id = ?', row.id)?.n ?? 0,
    createdAt: row.created_at,
  };
}

export function serializeEvalCase(row: EvalCaseRow) {
  return {
    id: row.id,
    setId: row.set_id,
    question: row.question,
    expectedAnswer: row.expected_answer,
    documentId: row.document_id,
    tags: safeJsonParse<string[]>(row.tags, []),
    source: row.source,
    createdAt: row.created_at,
  };
}

export function serializeEvalReview(row: EvalReviewRow) {
  return {
    variant: row.variant,
    answerCorrect: row.answer_correct === 1,
    citationsCorrect: row.citations_correct === 1,
    comment: row.comment ?? undefined,
    reviewerName: row.reviewer_name,
    reviewedAt: row.reviewed_at,
  };
}

export function reviewsForEvalResult(db: Database, resultId: string) {
  const rows = all<EvalReviewRow>(db, 'SELECT * FROM eval_result_reviews WHERE result_id = ?', resultId);
  const reviews: Record<string, ReturnType<typeof serializeEvalReview>> = {};
  for (const row of rows) reviews[row.variant] = serializeEvalReview(row);
  return reviews;
}

export function serializeEvalResult(db: Database, row: Record<string, unknown>) {
  return {
    id: String(row.id),
    caseId: String(row.case_id),
    question: String(row.question),
    expectedAnswer: String(row.expected_answer),
    answerA: String(row.answer_a ?? ''),
    answerB: row.answer_b === null || row.answer_b === undefined ? undefined : String(row.answer_b),
    citationsA: safeJsonParse(String(row.citations_a ?? '[]'), []),
    citationsB: row.citations_b ? safeJsonParse(String(row.citations_b), []) : undefined,
    scoresA: safeJsonParse(String(row.scores_a ?? '{}'), {}),
    scoresB: row.scores_b ? safeJsonParse(String(row.scores_b), {}) : undefined,
    latencyMs: Number(row.latency_ms ?? 0),
    reviews: reviewsForEvalResult(db, String(row.id)),
  };
}

export interface HumanVariantMetrics {
  reviewed: number;
  total: number;
  answerAccuracy: number | null;
  citationAccuracy: number | null;
}

export function computeHumanMetrics(
  db: Database,
  runId: string,
  hasVariantB: boolean,
): { A: HumanVariantMetrics; B?: HumanVariantMetrics } {
  const total = get<{ n: number }>(db, 'SELECT COUNT(*) AS n FROM eval_results WHERE run_id = ?', runId)?.n ?? 0;
  const rows = all<{ variant: 'A' | 'B'; answer_correct: number; citations_correct: number; n: number }>(
    db,
    `SELECT r.variant, r.answer_correct, r.citations_correct, COUNT(*) AS n
     FROM eval_result_reviews r
     JOIN eval_results e ON e.id = r.result_id
     WHERE e.run_id = ?
     GROUP BY r.variant, r.answer_correct, r.citations_correct`,
    runId,
  );
  const variantMetrics = (variant: 'A' | 'B'): HumanVariantMetrics => {
    const relevant = rows.filter((row) => row.variant === variant);
    const reviewed = relevant.reduce((sum, row) => sum + row.n, 0);
    const answerCorrect = relevant
      .filter((row) => row.answer_correct === 1)
      .reduce((sum, row) => sum + row.n, 0);
    const citationsCorrect = relevant
      .filter((row) => row.citations_correct === 1)
      .reduce((sum, row) => sum + row.n, 0);
    return {
      reviewed,
      total,
      answerAccuracy: reviewed > 0 ? answerCorrect / reviewed : null,
      citationAccuracy: reviewed > 0 ? citationsCorrect / reviewed : null,
    };
  };
  return {
    A: variantMetrics('A'),
    ...(hasVariantB ? { B: variantMetrics('B') } : {}),
  };
}

export function serializeEvalRun(db: Database, row: EvalRunRow, includeResults = false) {
  const hasVariantB = Boolean(row.config_b);
  const results = includeResults
    ? all<Record<string, unknown>>(db, 'SELECT * FROM eval_results WHERE run_id = ? ORDER BY created_at', row.id).map(
        (result) => serializeEvalResult(db, result),
      )
    : undefined;
  return {
    id: row.id,
    setId: row.set_id,
    name: row.name,
    status: row.status,
    progress: row.progress,
    configA: safeJsonParse(row.config_a, {}),
    configB: row.config_b ? safeJsonParse(row.config_b, {}) : undefined,
    metricsA: row.metrics_a ? safeJsonParse(row.metrics_a, undefined) : undefined,
    metricsB: row.metrics_b ? safeJsonParse(row.metrics_b, undefined) : undefined,
    humanMetrics: computeHumanMetrics(db, row.id, hasVariantB),
    error: row.error ?? undefined,
    createdAt: row.created_at,
    startedAt: row.started_at ?? undefined,
    finishedAt: row.finished_at ?? undefined,
    ...(includeResults ? { results } : {}),
  };
}
