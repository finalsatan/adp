import type { Model, RetrievalConfig } from '../api/types';
import { Field, Input, Select } from './ui';
import { RETRIEVAL_MODE_LABELS } from '../utils/permissions';
import { formatNumber } from '../utils/format';

export function RetrievalConfigFields({
  value,
  onChange,
  models,
  layout = 'grid',
  showAdvanced = true,
}: {
  value: RetrievalConfig;
  onChange: (value: RetrievalConfig) => void;
  models: Model[];
  layout?: 'grid' | 'stacked';
  showAdvanced?: boolean;
}) {
  const rerankModels = models.filter((model) => model.kind === 'rerank' && model.enabled);
  const chatModels = models.filter((model) => model.kind === 'chat' && model.enabled);

  return (
    <div className={`retrieval-fields retrieval-${layout}`}>
      <div className="retrieval-mode-group">
        <div className="retrieval-mode-label">检索方式</div>
        <div className="mode-cards">
          {(['hybrid', 'keyword', 'vector'] as const).map((mode) => (
            <button
              key={mode}
              type="button"
              className={`mode-card${value.mode === mode ? ' is-active' : ''}`}
              onClick={() => onChange({ ...value, mode })}
            >
              <strong>{RETRIEVAL_MODE_LABELS[mode]}</strong>
              <span>
                {mode === 'hybrid'
                  ? '关键词与向量 RRF 融合'
                  : mode === 'keyword'
                    ? '中文词项与 BM25 召回'
                    : 'Embedding 余弦召回'}
              </span>
            </button>
          ))}
        </div>
      </div>
      <div className="form-grid three">
        <Field label="召回数量 topK" htmlFor="retrieval-topk" hint="进入重排与上下文的候选数量。">
          <Input
            id="retrieval-topk"
            type="number"
            min={1}
            max={50}
            value={value.topK}
            onChange={(event) => onChange({ ...value, topK: Number(event.target.value) })}
          />
        </Field>
        <Field
          label="相关度阈值"
          htmlFor="retrieval-threshold"
          hint={`0 表示不过滤；当前 ${formatNumber(value.threshold)}。`}
        >
          <div className="threshold-row">
            <input
              type="range"
              min={0}
              max={1}
              step={0.01}
              value={value.threshold}
              onChange={(event) => onChange({ ...value, threshold: Number(event.target.value) })}
              aria-label="相关度阈值滑块"
            />
            <Input
              id="retrieval-threshold"
              type="number"
              min={0}
              max={1}
              step={0.01}
              value={value.threshold}
              onChange={(event) => onChange({ ...value, threshold: Number(event.target.value) })}
              className="threshold-input"
            />
          </div>
        </Field>
        <Field label="检索结果数量" hint="由服务端按阈值与 topK 约束后返回。">
          <div className="readonly-value">topK = {value.topK}</div>
        </Field>
      </div>

      {showAdvanced && (
        <div className="form-grid two">
          <Field
            label="重排模型"
            htmlFor="retrieval-rerank"
            hint={rerankModels.length === 0 ? '尚未登记可用的 rerank 模型。' : '对召回结果重新排序，阶段结果会写入链路追踪。'}
          >
            <Select
              id="retrieval-rerank"
              value={value.rerankModelId ?? ''}
              onChange={(event) => onChange({ ...value, rerankModelId: event.target.value || null })}
            >
              <option value="">不启用重排</option>
              {rerankModels.map((model) => (
                <option key={model.id} value={model.id}>
                  {model.name} · {model.model}
                </option>
              ))}
            </Select>
          </Field>
          <Field
            label="查询改写模型"
            htmlFor="retrieval-rewrite"
            hint={chatModels.length === 0 ? '尚未登记可用的 chat 模型。' : '改写会消耗模型 Token，过程可在 trace 中回放。'}
          >
            <Select
              id="retrieval-rewrite"
              value={value.rewriteModelId ?? ''}
              onChange={(event) => onChange({ ...value, rewriteModelId: event.target.value || null })}
            >
              <option value="">不改写查询</option>
              {chatModels.map((model) => (
                <option key={model.id} value={model.id}>
                  {model.name} · {model.model}
                  {model.provider === 'demo' ? '（本地演示）' : ''}
                </option>
              ))}
            </Select>
          </Field>
        </div>
      )}
    </div>
  );
}
