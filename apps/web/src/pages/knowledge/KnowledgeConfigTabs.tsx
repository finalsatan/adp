import { useEffect, useState } from 'react';
import {
  BookOpen,
  Layers,
  RefreshCw,
  Save,
  Scissors,
  SlidersHorizontal,
  Sparkles,
} from 'lucide-react';
import type { ChunkConfig, DocumentItem, KnowledgeBase, Model, RetrievalConfig } from '../../api/types';
import { documentApi, knowledgeApi } from '../../api/client';
import { Button, Callout, Card, Field, Input, SectionHeader, useConfirm } from '../../components/ui';
import { RetrievalConfigFields } from '../../components/RetrievalConfigFields';
import { useToast } from '../../contexts/ToastContext';
import {
  DEFAULT_CHUNK_CONFIG,
  DEFAULT_RETRIEVAL_CONFIG,
  STRATEGY_DESCRIPTIONS,
} from '../../utils/permissions';
import { errorMessage, formatNumber } from '../../utils/format';
import { isPendingDocument } from '../../utils/permissions';

export function KnowledgeParsingTab({
  kb,
  documents,
  canWrite,
  onSaved,
}: {
  kb: KnowledgeBase;
  documents: DocumentItem[];
  canWrite: boolean;
  onSaved: () => void;
}) {
  const toast = useToast();
  const confirm = useConfirm();
  const [config, setConfig] = useState<ChunkConfig>(kb.chunkConfig ?? DEFAULT_CHUNK_CONFIG);
  const [saving, setSaving] = useState(false);
  const [reprocessing, setReprocessing] = useState(false);

  useEffect(() => {
    setConfig(kb.chunkConfig ?? DEFAULT_CHUNK_CONFIG);
  }, [kb.id]);

  const pending = documents.filter((document) => isPendingDocument(document.status)).length;
  const failed = documents.filter((document) => document.status === 'FAILED').length;

  const save = async () => {
    setSaving(true);
    try {
      await knowledgeApi.update(kb.id, { chunkConfig: config });
      toast.success('分块配置已保存。对已有文档重新处理后会生效。');
      onSaved();
    } catch (error) {
      toast.error(errorMessage(error));
    } finally {
      setSaving(false);
    }
  };

  const reprocessAll = async () => {
    if (
      !(await confirm({
        title: '重新处理全部文档？',
        description: `将按当前分块配置重新解析并重建 ${documents.length} 个文档的索引。旧索引在新版本就绪前保留；手动编辑过的分块可能被覆盖。`,
        confirmLabel: '重新处理',
      }))
    ) {
      return;
    }
    setReprocessing(true);
    let success = 0;
    let failure = 0;
    for (const document of documents) {
      try {
        await documentApi.reprocess(document.id);
        success += 1;
      } catch {
        failure += 1;
      }
    }
    setReprocessing(false);
    if (failure === 0) toast.success(`${success} 个文档已加入处理队列`);
    else toast.error(`${success} 个文档已加入队列，${failure} 个提交失败`);
    onSaved();
  };

  return (
    <div className="tab-stack">
      <Card>
        <SectionHeader
          title="分块策略"
          description="分块决定检索粒度与上下文完整性。保存后需重新处理文档才会应用。"
        />
        <div className="strategy-grid">
          {(
            [
              { key: 'structure', icon: <BookOpen size={18} />, title: '结构分块' },
              { key: 'semantic', icon: <Sparkles size={18} />, title: '语义分块' },
              { key: 'parent-child', icon: <Layers size={18} />, title: '父子分块' },
            ] as const
          ).map((item) => (
            <button
              key={item.key}
              type="button"
              disabled={!canWrite}
              className={`strategy-card${config.strategy === item.key ? ' is-active' : ''}`}
              onClick={() => setConfig({ ...config, strategy: item.key })}
            >
              <span className="strategy-icon">{item.icon}</span>
              <strong>{item.title}</strong>
              <span>{STRATEGY_DESCRIPTIONS[item.key]}</span>
            </button>
          ))}
        </div>

        <div className="form-grid three strategy-numbers">
          <Field label="最大字符数" htmlFor="chunk-maxchars" hint="超长段落会按边界继续切分，不会切断表格行。">
            <Input
              id="chunk-maxchars"
              type="number"
              min={100}
              max={8000}
              value={config.maxChars}
              disabled={!canWrite}
              onChange={(event) => setConfig({ ...config, maxChars: Number(event.target.value) })}
            />
          </Field>
          <Field label="重叠字符数" htmlFor="chunk-overlap" hint="用于保持跨块上下文，建议不超过最大字符数的 20%。">
            <Input
              id="chunk-overlap"
              type="number"
              min={0}
              max={config.maxChars}
              value={config.overlap}
              disabled={!canWrite}
              onChange={(event) => setConfig({ ...config, overlap: Number(event.target.value) })}
            />
          </Field>
          <Field label="当前生效配置" hint="来自知识库设置，服务端保存。">
            <div className="config-readout">
              <span>{STRATEGY_DESCRIPTIONS[kb.chunkConfig?.strategy ?? 'structure'].split('，')[0]}</span>
              <code>
                maxChars {kb.chunkConfig?.maxChars ?? 800} · overlap {kb.chunkConfig?.overlap ?? 100}
              </code>
            </div>
          </Field>
        </div>

        {canWrite ? (
          <div className="form-actions">
            <Button variant="primary" icon={<Save size={15} />} loading={saving} onClick={save}>
              保存分块配置
            </Button>
            <Button
              variant="secondary"
              icon={<RefreshCw size={15} />}
              loading={reprocessing}
              disabled={documents.length === 0}
              onClick={reprocessAll}
            >
              重新处理全部文档
            </Button>
          </div>
        ) : (
          <Callout tone="info">查看权限下无法修改分块配置。</Callout>
        )}
      </Card>

      <div className="split-cards">
        <Card>
          <SectionHeader title="处理概况" description="根据文档列表实时统计。" />
          <div className="mini-stat-grid">
            <div className="mini-stat">
              <span>文档总数</span>
              <strong>{formatNumber(documents.length)}</strong>
            </div>
            <div className="mini-stat">
              <span>处理中</span>
              <strong>{formatNumber(pending)}</strong>
            </div>
            <div className="mini-stat">
              <span>失败</span>
              <strong className={failed > 0 ? 'text-danger' : ''}>{formatNumber(failed)}</strong>
            </div>
            <div className="mini-stat">
              <span>切片总数</span>
              <strong>{formatNumber(kb.chunkCount)}</strong>
            </div>
          </div>
        </Card>
        <Card>
          <SectionHeader title="分块查看与编辑" description="在文档详情中查看解析文本、引用位置并编辑分块。" />
          <div className="hint-list">
            <div>
              <Scissors size={15} />
              可在光标处拆分一个分块，或与下一块合并。
            </div>
            <div>
              <SlidersHorizontal size={15} />
              禁用后的分块不会被检索，但保留在文档版本中。
            </div>
            <div>
              <Layers size={15} />
              父子分块会标注父块与子块，引用仍定位到子块。
            </div>
          </div>
          <p className="muted-text">打开任一文档的「分块」即可检查解析结果与源位置。</p>
        </Card>
      </div>
    </div>
  );
}

export function KnowledgeRetrievalTab({
  kb,
  models,
  canWrite,
  onSaved,
}: {
  kb: KnowledgeBase;
  models: Model[];
  canWrite: boolean;
  onSaved: () => void;
}) {
  const toast = useToast();
  const [config, setConfig] = useState<RetrievalConfig>(kb.retrievalConfig ?? DEFAULT_RETRIEVAL_CONFIG);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setConfig(kb.retrievalConfig ?? DEFAULT_RETRIEVAL_CONFIG);
  }, [kb.id]);

  const save = async () => {
    setSaving(true);
    try {
      await knowledgeApi.update(kb.id, { retrievalConfig: config });
      toast.success('检索配置已保存，下一次检索测试与问答生效');
      onSaved();
    } catch (error) {
      toast.error(errorMessage(error));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="tab-stack">
      <Card>
        <SectionHeader
          title="默认检索配置"
          description="知识库级默认值。应用与评测运行可覆盖这些参数，实际配置会记录在链路追踪中。"
        />
        <RetrievalConfigFields value={config} onChange={setConfig} models={models} />
        {canWrite ? (
          <div className="form-actions">
            <Button variant="primary" icon={<Save size={15} />} loading={saving} onClick={save}>
              保存检索配置
            </Button>
          </div>
        ) : (
          <Callout tone="info">查看权限下无法修改检索配置。</Callout>
        )}
      </Card>

      <div className="split-cards">
        <Card>
          <SectionHeader title="混合检索如何工作" />
          <ol className="process-list">
            <li>
              <span>1</span>
              <div>
                <strong>关键词召回</strong>
                <p>中文分词与 BM25 匹配，覆盖专有名词与编号。</p>
              </div>
            </li>
            <li>
              <span>2</span>
              <div>
                <strong>向量召回</strong>
                <p>使用知识库绑定的 Embedding 模型计算余弦相似度。</p>
              </div>
            </li>
            <li>
              <span>3</span>
              <div>
                <strong>RRF 融合</strong>
                <p>按排名融合两路结果，再按阈值过滤并截取 topK。</p>
              </div>
            </li>
            <li>
              <span>4</span>
              <div>
                <strong>可选重排与改写</strong>
                <p>重排模型重新打分；改写模型的最终查询会写入 trace。</p>
              </div>
            </li>
          </ol>
        </Card>
        <Card>
          <SectionHeader title="配置提示" />
          <div className="hint-list">
            <div>
              <strong>阈值</strong>
              过高会减少召回甚至拒答；可先在检索测试中观察得分分布。
            </div>
            <div>
              <strong>topK</strong>
              同时影响延迟与上下文长度，建议从 5–10 开始。
            </div>
            <div>
              <strong>禁用分块</strong>
              任何检索方式都不会返回已禁用的分块。
            </div>
          </div>
          <Callout tone="warn">
            检索参数没有脱离数据的“最佳值”。请用评测集对比两套配置，并保留业务确认后的指标口径。
          </Callout>
        </Card>
      </div>
    </div>
  );
}
