import fs from 'node:fs';
import path from 'node:path';
import {
  get,
  run,
  transaction,
  type Database,
  type DocumentRow,
  type KnowledgeBaseRow,
  type ModelRow,
} from '../db/index.js';
import type { AppConfig } from '../config.js';
import { ParserError } from '../lib/errors.js';
import { newId, nowIso, sanitizeFileName, safeJsonParse, type SecretBox } from '../lib/util.js';
import { chunkDocument, type ChunkConfig, type DraftChunk } from './chunker.js';
import { embedTexts } from './models.js';
import { openModelKey } from './modelKeys.js';
import { parseDocument, type ParsedElement, type ParseResult } from './parser.js';
import { addSpan, finishTrace, startTrace } from './trace.js';
import { findDefaultEmbeddingModel, seedDemoModels } from './seed.js';

export class DocumentFiles {
  readonly filesDir: string;

  constructor(dataDir: string) {
    this.filesDir = path.join(dataDir, 'files');
    fs.mkdirSync(this.filesDir, { recursive: true });
  }

  store(documentId: string, version: number, fileName: string, buffer: Buffer): string {
    const dir = path.join(this.filesDir, documentId);
    fs.mkdirSync(dir, { recursive: true });
    const safeName = sanitizeFileName(fileName);
    const relative = `${documentId}/${version}-${safeName}`;
    fs.writeFileSync(path.join(this.filesDir, relative), buffer, { mode: 0o600 });
    return relative;
  }

  resolve(relative: string | null): string | null {
    if (!relative) return null;
    const full = path.resolve(this.filesDir, relative);
    const root = path.resolve(this.filesDir) + path.sep;
    if (!full.startsWith(root)) throw new ParserError('文档存储路径无效');
    return full;
  }

  remove(relative: string | null): void {
    const full = this.resolve(relative);
    if (!full) return;
    try {
      fs.unlinkSync(full);
    } catch {
      // already gone
    }
  }

  removeDocument(documentId: string): void {
    const dir = path.join(this.filesDir, documentId);
    fs.rmSync(dir, { recursive: true, force: true });
  }
}

interface IngestDeps {
  db: Database;
  config: AppConfig;
  secretBox: SecretBox;
  files: DocumentFiles;
  updateJobProgress?: (jobId: string, progress: number) => void;
  jobId?: string;
}

interface EmbedCache {
  vectors: Map<string, number[]>;
  tokenCount: number;
  dimensions: number;
}

export class IngestService {
  constructor(private readonly deps: IngestDeps) {}

  private guard: { documentId: string; version: number; sourcePath: string | null } | null = null;

  private isStale(): boolean {
    if (!this.guard) return false;
    const current = get<DocumentRow>(this.deps.db, 'SELECT * FROM documents WHERE id = ?', this.guard.documentId);
    return (
      !current ||
      current.version !== this.guard.version ||
      current.source_path !== this.guard.sourcePath
    );
  }

  private progress(docId: string, progress: number, status?: DocumentRow['status']): void {
    const now = nowIso();
    const version = this.guard?.version;
    const sourcePath = this.guard?.sourcePath ?? null;
    if (version === undefined) return;
    if (status) {
      run(
        this.deps.db,
        'UPDATE documents SET status = ?, progress = ?, updated_at = ? WHERE id = ? AND version = ? AND source_path IS ?',
        status,
        progress,
        now,
        docId,
        version,
        sourcePath,
      );
    } else {
      run(
        this.deps.db,
        'UPDATE documents SET progress = ?, updated_at = ? WHERE id = ? AND version = ? AND source_path IS ?',
        progress,
        now,
        docId,
        version,
        sourcePath,
      );
    }
    if (this.deps.jobId && this.deps.updateJobProgress) {
      this.deps.updateJobProgress(this.deps.jobId, progress);
    }
  }

  async process(documentId: string): Promise<void> {
    const { db, config, files, secretBox } = this.deps;
    seedDemoModels(db);
    const document = get<DocumentRow>(db, 'SELECT * FROM documents WHERE id = ?', documentId);
    if (!document) throw new Error('文档不存在或已删除');
    const kb = get<KnowledgeBaseRow>(db, 'SELECT * FROM knowledge_bases WHERE id = ?', document.kb_id);
    if (!kb) throw new Error('知识库不存在或已删除');
    this.guard = { documentId: document.id, version: document.version, sourcePath: document.source_path };
    const trace = startTrace(db, {
      kind: 'ingest',
      name: `文档入库：${document.name}`,
      orgId: kb.org_id,
      input: { documentId, kbId: kb.id, version: document.version, mime: document.mime },
    });
    let tokenCount = 0;

    try {
      this.progress(document.id, 5, 'PARSING');
      const sourcePath = files.resolve(document.source_path);
      if (!sourcePath || !fs.existsSync(sourcePath)) throw new ParserError('文档源文件不存在，请重新上传');
      const buffer = fs.readFileSync(sourcePath);

      const parseStarted = performance.now();
      const parsed: ParseResult = await parseDocument(
        { fileName: document.name, mime: document.mime, buffer },
        {
          parserUrl: config.parserUrl,
          parserToken: config.parserToken,
          timeoutMs: config.parserTimeoutMs,
        },
      );
      addSpan(db, trace.id, {
        name: 'parse',
        kind: 'parser',
        durationMs: performance.now() - parseStarted,
        input: { fileName: document.name, mime: document.mime, size: buffer.length },
        output: {
          engine: parsed.engine,
          elements: parsed.elements.length,
          chars: parsed.text.length,
          warnings: parsed.warnings,
        },
      });
      this.progress(document.id, 35, 'PROCESSING');

      const chunkConfig = safeJsonParse<ChunkConfig>(kb.chunk_config, {
        strategy: 'structure',
        maxChars: 800,
        overlap: 100,
      });
      const embeddingModel = this.resolveEmbeddingModel(kb);
      const cache: EmbedCache = { vectors: new Map(), tokenCount: 0, dimensions: embeddingModel.dimensions ?? 0 };

      const embed = async (texts: string[]): Promise<number[][]> => {
        const missing = [...new Set(texts.filter((text) => !cache.vectors.has(text)))];
        if (missing.length > 0) {
          const result = await embedTexts(
            embeddingModel,
            openModelKey(secretBox, embeddingModel),
            missing,
            { timeoutMs: config.modelTimeoutMs },
          );
          missing.forEach((text, index) => {
            const vector = result.vectors[index];
            if (vector) cache.vectors.set(text, vector);
          });
          cache.tokenCount += result.usage?.totalTokens ?? 0;
          cache.dimensions = result.dimensions;
          if (!embeddingModel.dimensions && result.dimensions > 0) {
            embeddingModel.dimensions = result.dimensions;
            run(db, 'UPDATE models SET dimensions = ? WHERE id = ?', result.dimensions, embeddingModel.id);
          }
        }
        return texts.map((text) => cache.vectors.get(text) ?? []);
      };

      const chunkStarted = performance.now();
      const drafts = await chunkDocument(parsed.elements, chunkConfig, embed);
      if (drafts.length === 0) throw new Error('分块结果为空，请检查文档内容或分块配置');
      addSpan(db, trace.id, {
        name: 'chunk',
        kind: 'chunking',
        durationMs: performance.now() - chunkStarted,
        input: { strategy: chunkConfig.strategy, maxChars: chunkConfig.maxChars, overlap: chunkConfig.overlap },
        output: {
          chunks: drafts.length,
          parentChunks: drafts.filter((draft) => draft.isParent).length,
        },
      });
      this.progress(document.id, 65, 'PROCESSING');

      const embedStarted = performance.now();
      const indexable = drafts.filter((draft) => !draft.isParent);
      const vectors = await embed(indexable.map((draft) => draft.content));
      if (vectors.some((vector) => vector.length === 0)) {
        throw new Error('部分分块没有得到 Embedding 向量，已保留旧索引');
      }
      tokenCount += cache.tokenCount;
      addSpan(db, trace.id, {
        name: 'embed',
        kind: 'embedding',
        durationMs: performance.now() - embedStarted,
        input: {
          model: embeddingModel.name,
          provider: embeddingModel.provider,
          chunks: indexable.length,
        },
        output: {
          dimensions: cache.dimensions,
          demo: embeddingModel.provider === 'demo',
          note:
            embeddingModel.provider === 'demo'
              ? '本地演示词面哈希向量，不代表语义质量'
              : undefined,
        },
      });
      this.progress(document.id, 85, 'PROCESSING');

      if (this.isStale()) {
        finishTrace(db, trace, {
          status: 'FAILED',
          tokenCount: tokenCount || undefined,
          output: { stale: true, message: '文档版本已被替换，旧入库任务未写入' },
        });
        return;
      }

      const now = nowIso();
      transaction(db, () => {
        run(db, 'DELETE FROM chunks WHERE document_id = ?', document.id);
        const inserted: string[] = [];
        const vectorByIndex = new Map<number, number[]>();
        let vectorCursor = 0;
        drafts.forEach((draft, index) => {
          if (!draft.isParent) {
            vectorByIndex.set(index, vectors[vectorCursor] ?? []);
            vectorCursor += 1;
          }
        });
        drafts.forEach((draft: DraftChunk, index) => {
          const id = newId();
          inserted.push(id);
          const parentId =
            draft.parentIndex === undefined ? null : inserted[draft.parentIndex] ?? null;
          run(
            db,
            `INSERT INTO chunks
             (id, document_id, kb_id, content, heading_path, position, enabled, edited, parent_id, is_parent, source_ref, created_at, updated_at)
             VALUES (?, ?, ?, ?, ?, ?, 1, 0, ?, ?, ?, ?, ?)`,
            id,
            document.id,
            kb.id,
            draft.content,
            JSON.stringify(draft.headingPath),
            index,
            parentId,
            draft.isParent ? 1 : 0,
            JSON.stringify(draft.sourceRef),
            now,
            now,
          );
          const vector = vectorByIndex.get(index);
          if (vector && vector.length > 0) {
            run(
              db,
              'INSERT INTO chunk_embeddings (chunk_id, model_id, vector, dimensions, created_at) VALUES (?, ?, ?, ?, ?)',
              id,
              embeddingModel.id,
              JSON.stringify(vector),
              vector.length,
              now,
            );
          }
        });
        run(
          db,
          `UPDATE documents
           SET status = 'READY', progress = 100, error = NULL, chunk_count = ?, parsed_text = ?,
               parsed_elements = ?, updated_at = ?
           WHERE id = ?`,
          drafts.length,
          parsed.text,
          JSON.stringify(parsed.elements),
          now,
          document.id,
        );
      });

      finishTrace(db, trace, {
        status: 'OK',
        tokenCount: tokenCount || undefined,
        output: {
          chunks: drafts.length,
          parentChunks: drafts.filter((draft) => draft.isParent).length,
          engine: parsed.engine,
          warnings: parsed.warnings,
        },
      });
    } catch (error) {
      const message = redactIngestError(error, config);
      if (this.isStale()) {
        finishTrace(db, trace, {
          status: 'FAILED',
          tokenCount: tokenCount || undefined,
          output: { stale: true, message: '文档版本已被替换，旧入库任务异常未写入' },
        });
        return;
      }
      run(
        db,
        `UPDATE documents SET status = 'FAILED', error = ?, updated_at = ? WHERE id = ? AND version = ? AND source_path IS ?`,
        message,
        nowIso(),
        document.id,
        this.guard?.version ?? document.version,
        this.guard?.sourcePath ?? document.source_path,
      );
      finishTrace(db, trace, { status: 'FAILED', tokenCount: tokenCount || undefined, output: { error: message } });
      throw error;
    }
  }

  private resolveEmbeddingModel(kb: KnowledgeBaseRow): ModelRow {
    const id = kb.embedding_model_id;
    let model = id ? get<ModelRow>(this.deps.db, 'SELECT * FROM models WHERE id = ?', id) : undefined;
    if (!model) model = findDefaultEmbeddingModel(this.deps.db, kb.org_id);
    if (!model) throw new Error('知识库未配置 Embedding 模型');
    if (model.kind !== 'embedding') throw new Error('知识库配置的模型不是 Embedding 模型');
    if (model.enabled !== 1) throw new Error(`Embedding 模型「${model.name}」已禁用`);
    if (model.scope === 'organization' && model.org_id !== kb.org_id) {
      throw new Error('知识库不能使用其他组织的 Embedding 模型');
    }
    return model;
  }
}

export function redactIngestError(error: unknown, config: AppConfig): string {
  let message = error instanceof Error ? error.message : String(error);
  if (config.parserToken) message = message.split(config.parserToken).join('[REDACTED]');
  return message.slice(0, 4000);
}

export function parsedElementsOf(document: DocumentRow): ParsedElement[] {
  return safeJsonParse<ParsedElement[]>(document.parsed_elements, []);
}
