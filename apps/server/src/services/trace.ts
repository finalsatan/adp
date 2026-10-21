import { all, get, run, type Database, type SpanRow, type TraceRow } from '../db/index.js';
import { newId, nowIso, safeJsonParse } from '../lib/util.js';

export interface TraceOptions {
  kind: 'ingest' | 'search' | 'chat' | 'eval';
  name: string;
  orgId?: string | null;
  userId?: string | null;
  appId?: string | null;
  input?: unknown;
}

export interface TraceFinish {
  status?: 'OK' | 'FAILED';
  tokenCount?: number | null;
  output?: unknown;
}

export function truncateForTrace(value: unknown, max = 16_000): string {
  const text = typeof value === 'string' ? value : JSON.stringify(value ?? null);
  if (text.length <= max) return text;
  return `${text.slice(0, max)}…[截断]`;
}

export function startTrace(db: Database, options: TraceOptions): { id: string; startedAt: number } {
  const id = newId();
  run(
    db,
    `INSERT INTO traces (id, kind, name, status, duration_ms, token_count, app_id, org_id, user_id, input, output, created_at)
     VALUES (?, ?, ?, 'FAILED', 0, NULL, ?, ?, ?, ?, NULL, ?)`,
    id,
    options.kind,
    options.name,
    options.appId ?? null,
    options.orgId ?? null,
    options.userId ?? null,
    options.input === undefined ? null : truncateForTrace(options.input),
    nowIso(),
  );
  return { id, startedAt: performance.now() };
}

export function addSpan(
  db: Database,
  traceId: string,
  span: {
    name: string;
    kind: string;
    durationMs: number;
    input?: unknown;
    output?: unknown;
    status?: 'OK' | 'FAILED';
  },
): string {
  const id = newId();
  const position = (get<{ n: number }>(db, 'SELECT COUNT(*) AS n FROM spans WHERE trace_id = ?', traceId)?.n ?? 0) + 1;
  run(
    db,
    `INSERT INTO spans (id, trace_id, name, kind, duration_ms, input, output, status, position, created_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    id,
    traceId,
    span.name,
    span.kind,
    Math.round(span.durationMs),
    span.input === undefined ? null : truncateForTrace(span.input),
    span.output === undefined ? null : truncateForTrace(span.output),
    span.status ?? 'OK',
    position,
    nowIso(),
  );
  return id;
}

export async function spanAsync<T>(
  db: Database,
  traceId: string,
  name: string,
  kind: string,
  fn: () => Promise<T> | T,
  summarize?: (value: T) => unknown,
): Promise<T> {
  const started = performance.now();
  try {
    const value = await fn();
    addSpan(db, traceId, {
      name,
      kind,
      durationMs: performance.now() - started,
      output: summarize ? summarize(value) : value,
      status: 'OK',
    });
    return value;
  } catch (error) {
    addSpan(db, traceId, {
      name,
      kind,
      durationMs: performance.now() - started,
      output: { error: error instanceof Error ? error.message : String(error) },
      status: 'FAILED',
    });
    throw error;
  }
}

export function finishTrace(
  db: Database,
  handle: { id: string; startedAt: number },
  finish: TraceFinish = {},
): void {
  run(
    db,
    'UPDATE traces SET status = ?, duration_ms = ?, token_count = ?, output = ? WHERE id = ?',
    finish.status ?? 'OK',
    Math.round(performance.now() - handle.startedAt),
    finish.tokenCount ?? null,
    finish.output === undefined ? null : truncateForTrace(finish.output),
    handle.id,
  );
}

export type SpanView = {
  id: string;
  name: string;
  kind: string;
  durationMs: number;
  input: unknown;
  output: unknown;
  status: string;
};

export function serializeSpan(row: SpanRow): SpanView {
  return {
    id: row.id,
    name: row.name,
    kind: row.kind,
    durationMs: row.duration_ms,
    input: safeJsonParse(row.input, row.input),
    output: safeJsonParse(row.output, row.output),
    status: row.status,
  };
}

export function serializeTrace(row: TraceRow) {
  return {
    id: row.id,
    kind: row.kind,
    name: row.name,
    status: row.status,
    durationMs: row.duration_ms,
    tokenCount: row.token_count ?? undefined,
    appId: row.app_id ?? undefined,
    orgId: row.org_id ?? undefined,
    createdAt: row.created_at,
  };
}

export function traceWithSpans(db: Database, row: TraceRow) {
  const spans = all<SpanRow>(db, 'SELECT * FROM spans WHERE trace_id = ? ORDER BY position ASC', row.id);
  return { ...serializeTrace(row), spans: spans.map(serializeSpan) };
}
