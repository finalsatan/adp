import { get, run, transaction, type Database, type JobRow } from '../db/index.js';
import { newId, nowIso, safeJsonParse } from '../lib/util.js';

export const JOB_TYPES = ['ingest', 'eval-generate', 'eval-run'] as const;
export type JobType = (typeof JOB_TYPES)[number];

export interface JobContext {
  jobId: string;
  job: JobRow;
  payload: Record<string, unknown>;
  setProgress: (progress: number) => void;
}

export type JobHandler = (context: JobContext) => Promise<void>;

export function enqueueJob(
  db: Database,
  type: JobType,
  payload: Record<string, unknown>,
  options: { maxAttempts?: number } = {},
): string {
  const id = newId();
  run(
    db,
    `INSERT INTO jobs (id, type, payload, status, progress, attempts, max_attempts, error, next_run_at, created_at, started_at, finished_at)
     VALUES (?, ?, ?, 'QUEUED', 0, 0, ?, NULL, NULL, ?, NULL, NULL)`,
    id,
    type,
    JSON.stringify(payload),
    options.maxAttempts ?? 2,
    nowIso(),
  );
  return id;
}

export class JobWorker {
  private timer: NodeJS.Timeout | null = null;
  private running: Promise<void> | null = null;
  private stopped = false;
  private readonly handlers = new Map<string, JobHandler>();

  constructor(
    private readonly db: Database,
    private readonly options: { intervalMs?: number; retryDelayMs?: number } = {},
  ) {}

  register(type: JobType, handler: JobHandler): void {
    this.handlers.set(type, handler);
  }

  recover(): void {
    run(
      this.db,
      `UPDATE jobs SET status = 'QUEUED', started_at = NULL,
         error = COALESCE(error, '服务重启后恢复任务')
       WHERE status = 'RUNNING'`,
    );
  }

  start(): void {
    if (this.timer) return;
    this.stopped = false;
    this.recover();
    this.timer = setInterval(() => void this.kick(), this.options.intervalMs ?? 250);
    if (typeof this.timer.unref === 'function') this.timer.unref();
    void this.kick();
  }

  async stop(): Promise<void> {
    this.stopped = true;
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
    if (this.running) await this.running;
  }

  kick(): Promise<void> {
    if (this.stopped) return Promise.resolve();
    if (this.running) return this.running;
    this.running = this.loop().finally(() => {
      this.running = null;
    });
    return this.running;
  }

  private claim(): JobRow | undefined {
    return transaction(this.db, () => {
      const job = get<JobRow>(
        this.db,
        `SELECT * FROM jobs
         WHERE status = 'QUEUED' AND (next_run_at IS NULL OR next_run_at <= ?)
         ORDER BY created_at ASC LIMIT 1`,
        nowIso(),
      );
      if (!job) return undefined;
      run(
        this.db,
        `UPDATE jobs SET status = 'RUNNING', attempts = attempts + 1, started_at = ?, next_run_at = NULL
         WHERE id = ?`,
        nowIso(),
        job.id,
      );
      return { ...job, status: 'RUNNING', attempts: job.attempts + 1 };
    });
  }

  private async loop(): Promise<void> {
    while (!this.stopped) {
      const job = this.claim();
      if (!job) return;
      const handler = this.handlers.get(job.type);
      if (!handler) {
        this.failJob(job, `未知任务类型：${job.type}`, false);
        continue;
      }
      try {
        await handler({
          jobId: job.id,
          job,
          payload: safeJsonParse<Record<string, unknown>>(job.payload, {}),
          setProgress: (progress: number) => {
            run(this.db, 'UPDATE jobs SET progress = ? WHERE id = ?', Math.max(0, Math.min(100, progress)), job.id);
          },
        });
        run(
          this.db,
          "UPDATE jobs SET status = 'COMPLETED', progress = 100, error = NULL, finished_at = ? WHERE id = ?",
          nowIso(),
          job.id,
        );
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        const retry = job.attempts < job.max_attempts;
        this.failJob(job, message, retry);
      }
    }
  }

  private failJob(job: JobRow, message: string, retry: boolean): void {
    if (retry) {
      run(
        this.db,
        `UPDATE jobs SET status = 'QUEUED', error = ?, next_run_at = ?, started_at = NULL,
           progress = 0 WHERE id = ?`,
        message.slice(0, 4000),
        new Date(Date.now() + (this.options.retryDelayMs ?? 750)).toISOString(),
        job.id,
      );
      if (job.type === 'eval-run') {
        const payload = safeJsonParse<Record<string, unknown>>(job.payload, {});
        if (typeof payload.runId === 'string') {
          run(
            this.db,
            "UPDATE eval_runs SET status = 'QUEUED', progress = 0, error = ?, finished_at = NULL WHERE id = ? AND status != 'COMPLETED'",
            message.slice(0, 4000),
            payload.runId,
          );
        }
      }
      return;
    }
    run(
      this.db,
      "UPDATE jobs SET status = 'FAILED', error = ?, finished_at = ? WHERE id = ?",
      message.slice(0, 4000),
      nowIso(),
      job.id,
    );
    if (job.type === 'eval-run') {
      const payload = safeJsonParse<Record<string, unknown>>(job.payload, {});
      if (typeof payload.runId === 'string') {
        run(
          this.db,
          "UPDATE eval_runs SET status = 'FAILED', error = ?, finished_at = ? WHERE id = ? AND status != 'COMPLETED'",
          message.slice(0, 4000),
          nowIso(),
          payload.runId,
        );
      }
    }
  }

  async waitForJob(jobId: string, timeoutMs = 20_000): Promise<JobRow> {
    const started = Date.now();
    while (Date.now() - started < timeoutMs) {
      const row = get<JobRow>(this.db, 'SELECT * FROM jobs WHERE id = ?', jobId);
      if (row && (row.status === 'COMPLETED' || row.status === 'FAILED')) return row;
      await new Promise((resolve) => setTimeout(resolve, 20));
      void this.kick();
    }
    throw new Error(`等待任务超时：${jobId}`);
  }

  async waitForIdle(timeoutMs = 20_000): Promise<void> {
    const started = Date.now();
    while (Date.now() - started < timeoutMs) {
      const pending = get<{ n: number }>(
        this.db,
        "SELECT COUNT(*) AS n FROM jobs WHERE status IN ('QUEUED','RUNNING')",
      )?.n;
      if ((pending ?? 0) === 0) return;
      await new Promise((resolve) => setTimeout(resolve, 25));
      void this.kick();
    }
    throw new Error('等待任务队列空闲超时');
  }
}
