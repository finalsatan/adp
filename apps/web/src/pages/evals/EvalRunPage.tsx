import { useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import {
  ArrowLeft,
  BarChart3,
  CheckCircle2,
  ChevronRight,
  Clock3,
  FileText,
  FlaskConical,
  MinusCircle,
  RefreshCw,
} from 'lucide-react';
import type { Citation, EvalMetrics, EvalResult, EvalReview, HumanMetrics, RetrievalConfig } from '../../api/types';
import { evalApi } from '../../api/client';
import { useApi, useDocumentTitle, usePolling } from '../../hooks';
import { useAuth } from '../../contexts/AuthContext';
import { useToast } from '../../contexts/ToastContext';
import {
  Badge,
  Button,
  Callout,
  Card,
  EmptyState,
  ErrorState,
  LoadingState,
  Modal,
  PageHeader,
  PermissionDenied,
  ProgressBar,
  SectionHeader,
  StatusBadge,
  Textarea,
} from '../../components/ui';
import {
  RETRIEVAL_MODE_LABELS,
} from '../../utils/permissions';
import {
  formatDateTime,
  formatDuration,
  formatNumber,
  formatPercent,
  normalizeProgress,
} from '../../utils/format';

export function EvalRunPage() {
  useDocumentTitle('评测运行');
  const { id = '', runId = '' } = useParams();
  const { can } = useAuth();
  const [selectedCaseId, setSelectedCaseId] = useState<string | null>(null);

  const runState = useApi(() => evalApi.run(runId), [runId], { enabled: Boolean(runId) });
  const run = runState.data;
  const active = run?.status === 'QUEUED' || run?.status === 'RUNNING';

  usePolling(() => runState.reload(), 4000, Boolean(active));

  const setState = useApi(
    () => evalApi.getSet(run?.setId ?? id),
    [run?.setId ?? id],
    { enabled: Boolean(run?.setId ?? id) },
  );

  const hasB = Boolean(run?.configB || run?.metricsB);
  const results = run?.results ?? [];
  const selected = useMemo(
    () => results.find((item) => item.caseId === selectedCaseId) ?? null,
    [results, selectedCaseId],
  );

  const summary = useMemo(() => {
    if (!run) return null;
    return {
      total: run.metricsA?.total ?? results.length,
      answeredB: results.filter((item) => Boolean(item.answerB)).length,
      casesWithCitations: results.filter((item) => (item.citationsA?.length ?? 0) > 0).length,
    };
  }, [run, results]);

  if (!can('eval:manage')) {
    return (
      <div className="page">
        <PageHeader title="评测运行" />
        <Card><PermissionDenied permission="eval:manage" /></Card>
      </div>
    );
  }

  if (runState.loading && !run) {
    return <div className="page"><LoadingState label="正在读取运行结果…" /></div>;
  }

  if (runState.error || !run) {
    return (
      <div className="page">
        <PageHeader
          title="评测运行"
          back={<Link to={`/evals/${id}`} className="back-link"><ArrowLeft size={15} /> 返回评测集</Link>}
        />
        <ErrorState error={runState.error ?? new Error('运行记录不存在')} onRetry={runState.reload} />
      </div>
    );
  }

  return (
    <div className="page">
      <PageHeader
        back={<Link to={`/evals/${run.setId}`} className="back-link"><ArrowLeft size={15} /> 返回评测集{setState.data ? ` · ${setState.data.name}` : ''}</Link>}
        eyebrow="效果评测 / 运行"
        title={
          <span className="title-with-badge">
            {run.name || '未命名运行'}
            <StatusBadge status={run.status} />
            {hasB && <Badge tone="indigo">双配置对比</Badge>}
          </span>
        }
        description={
          <span className="kb-meta-line">
            {formatDateTime(run.createdAt)} · 用例 {formatNumber(summary?.total ?? results.length)} 条
            {run.error ? ` · 失败原因：${run.error}` : ''}
          </span>
        }
        actions={
          <>
            <Button variant="secondary" icon={<RefreshCw size={15} />} onClick={runState.reload} loading={runState.loading}>
              刷新
            </Button>
            {setState.data && (
              <Link to={`/evals/${run.setId}`} className="btn btn-secondary btn-md">
                <FlaskConical size={15} />
                <span>评测集</span>
              </Link>
            )}
          </>
        }
      />

      {active && (
        <Callout tone="info" title={run.status === 'QUEUED' ? '运行已排队' : '运行正在进行'}>
          <div className="run-list-progress">
            <ProgressBar value={normalizeProgress(run.progress)} label={`${Math.round(normalizeProgress(run.progress))}%`} />
          </div>
          <span>页面每 4 秒自动刷新。运行使用配置快照与真实检索/模型调用。</span>
        </Callout>
      )}

      {run.status === 'FAILED' && (
        <Callout tone="danger" title="运行失败">
          {run.error || '服务端未返回失败原因，请查看后端任务日志或链路追踪。'}
        </Callout>
      )}

      {run.error && run.status !== 'FAILED' && (
        <Callout tone="warn" title="运行提示">
          {run.error}
        </Callout>
      )}

      <div className="stat-grid stat-grid-compact">
        <div className="stat-card">
          <div className="stat-icon"><FileText size={17} /></div>
          <div className="stat-copy"><span>评测用例</span><strong>{formatNumber(summary?.total ?? 0)}</strong></div>
        </div>
        <div className="stat-card">
          <div className="stat-icon"><BarChart3 size={17} /></div>
          <div className="stat-copy"><span>配置 A 指标</span><strong className="stat-small">{metricStatus(run.metricsA)}</strong></div>
        </div>
        {hasB && (
          <div className="stat-card">
            <div className="stat-icon"><BarChart3 size={17} /></div>
            <div className="stat-copy"><span>配置 B 指标</span><strong className="stat-small">{metricStatus(run.metricsB)}</strong></div>
          </div>
        )}
        <div className="stat-card">
          <div className="stat-icon"><CheckCircle2 size={17} /></div>
          <div className="stat-copy"><span>含引用的回答</span><strong>{formatNumber(summary?.casesWithCitations ?? 0)}</strong></div>
        </div>
        <div className="stat-card">
          <div className="stat-icon"><Clock3 size={17} /></div>
          <div className="stat-copy">
            <span>平均延迟</span>
            <strong className="stat-small">
              {run.metricsA?.avgLatencyMs == null ? '未标注' : formatDuration(run.metricsA.avgLatencyMs)}
            </strong>
          </div>
        </div>
      </div>

      <div className="split-cards run-config-cards">
        <Card>
          <SectionHeader title="配置 A 快照" description="运行时固化的检索配置。" />
          <ConfigSummary config={run.configA} tone="a" />
        </Card>
        {hasB ? (
          <Card>
            <SectionHeader title="配置 B 快照" description="与 A 在同一批用例上运行。" />
            {run.configB ? <ConfigSummary config={run.configB} tone="b" /> : <p className="muted-text">服务端未返回配置 B 快照。</p>}
          </Card>
        ) : (
          <Card>
            <SectionHeader title="单配置运行" description="本次只运行了配置 A。" />
            <p className="muted-text">如需比较两套检索配置，可在评测集中重新发起双配置运行。</p>
          </Card>
        )}
      </div>

      <Card>
        <SectionHeader
          title="指标明细"
          description="比例均为 0..1，页面按百分比展示。未标注的指标不参与判断，也不会显示为 0。"
        />
        <div className="metric-legend">
          <span><i className="legend-dot" /> 配置 A</span>
          {hasB && <span><i className="legend-dot b" /> 配置 B</span>}
        </div>
        <div className="metric-section">
          <MetricRow
            label="答案词项匹配"
            hint="词项匹配参考值，不是业务确认的正确率"
            a={run.metricsA?.answerMatch}
            b={run.metricsB?.answerMatch}
            hasB={hasB}
            format={(value) => formatPercent(value)}
            bar={(value) => value * 100}
          />
          <MetricRow
            label="引用准确率"
            hint="引用片段是否支撑答案，依赖评测实现与标注"
            a={run.metricsA?.citationAccuracy}
            b={run.metricsB?.citationAccuracy}
            hasB={hasB}
            format={(value) => formatPercent(value)}
            bar={(value) => value * 100}
          />
          <MetricRow
            label="检索召回率"
            hint="标准文档是否被召回，依赖标注文档"
            a={run.metricsA?.retrievalRecall}
            b={run.metricsB?.retrievalRecall}
            hasB={hasB}
            format={(value) => formatPercent(value)}
            bar={(value) => value * 100}
          />
          <MetricRow
            label="拒答率"
            hint="无依据时明确拒答的比例"
            a={run.metricsA?.refusalRate}
            b={run.metricsB?.refusalRate}
            hasB={hasB}
            format={(value) => formatPercent(value)}
            bar={(value) => value * 100}
          />
          <MetricRow
            label="平均延迟"
            hint="检索与回答的整体耗时"
            a={run.metricsA?.avgLatencyMs}
            b={run.metricsB?.avgLatencyMs}
            hasB={hasB}
            format={(value) => formatDuration(value)}
            bar={(value) => value}
            latency
          />
        </div>
        <div className="metric-note">
          <p className="muted-text">
            “答案匹配”以标准答案做词项匹配，只能作为回归参考。要将其称为业务正确率，必须以业务方确认的 200 条问题与标准答案完成人工验收。
          </p>
        </div>
      </Card>

      <Card>
        <SectionHeader
          title="业务人工评审"
          description="由业务评审人逐条判断答案与引用是否正确，与自动词项匹配独立。未评审的指标显示“未评审”，不会用 0 代替。"
        />
        <div className={`human-metrics${hasB ? '' : ' single'}`}>
          <HumanMetricCard label="配置 A" metrics={run.humanMetrics?.A} fallbackTotal={run.metricsA?.total ?? results.length} />
          {hasB && <HumanMetricCard label="配置 B" metrics={run.humanMetrics?.B} fallbackTotal={run.metricsB?.total ?? results.length} variant="b" />}
        </div>
      </Card>

      <Card padded={false}>
        <div className="card-toolbar">
          <div className="card-toolbar-stat">
            <FileText size={16} />
            <span>逐条结果</span>
            <span className="muted-text">共 {results.length} 条</span>
          </div>
        </div>
        {results.length === 0 ? (
          <EmptyState
            icon={<FlaskConical size={23} />}
            title={active ? '运行结果尚未产出' : '没有逐条结果'}
            description={
              active
                ? '用例仍在执行，完成后会自动刷新。若长时间无结果，请检查运行状态与后端任务日志。'
                : '该运行没有返回逐条结果，可能已失败或服务端未记录。'
            }
          />
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>问题</th>
                  <th>标准答案</th>
                  <th>回答 A</th>
                  {hasB && <th>回答 B</th>}
                  <th>评分</th>
                  <th className="align-right">详情</th>
                </tr>
              </thead>
              <tbody>
                {results.map((item) => (
                  <tr key={item.caseId}>
                    <td><div className="case-question-cell">{item.question}</div></td>
                    <td><div className="case-answer-cell">{item.expectedAnswer}</div></td>
                    <td><div className="result-answer">{item.answerA || '—'}</div></td>
                    {hasB && <td><div className="result-answer">{item.answerB || '—'}</div></td>}
                    <td>
                      <div className="result-scores">
                        <span>A 匹配 {scoreText(item.scoresA?.answerMatch)}</span>
                        <span>A 引用 {scoreText(item.scoresA?.citationAccuracy)}</span>
                        {hasB && <span>B 匹配 {scoreText(item.scoresB?.answerMatch)}</span>}
                      </div>
                    </td>
                    <td className="align-right">
                      <button type="button" className="row-action" onClick={() => setSelectedCaseId(item.caseId)}>
                        查看 <ChevronRight size={13} />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="card-foot-note">
          逐条结果保留回答与引用；评测不修改知识库，也不代表业务验收通过。
        </div>
      </Card>

      <ResultDetailModal
        result={selected}
        hasB={hasB}
        reviewEnabled={run.status === 'COMPLETED'}
        onClose={() => setSelectedCaseId(null)}
        onSaved={() => {
          runState.reload();
          setState.reload();
        }}
      />
    </div>
  );
}

function MetricRow({
  label,
  hint,
  a,
  b,
  hasB,
  format,
  bar,
  latency = false,
}: {
  label: string;
  hint: string;
  a: number | null | undefined;
  b: number | null | undefined;
  hasB: boolean;
  format: (value: number) => string;
  bar: (value: number) => number;
  latency?: boolean;
}) {
  const max = Math.max(a == null ? 0 : bar(a), b == null ? 0 : bar(b), 1);
  return (
    <div className={`metric-row${hasB ? '' : ' no-b'}`}>
      <div className="metric-label">
        <strong>{label}</strong>
        <span>{hint}</span>
      </div>
      <MetricValue value={a} format={format} width={a == null ? 0 : Math.min(100, (bar(a) / max) * 100)} label="A" latency={latency} />
      {hasB && (
        <MetricValue value={b} format={format} width={b == null ? 0 : Math.min(100, (bar(b) / max) * 100)} label="B" latency={latency} variant="b" />
      )}
    </div>
  );
}

function MetricValue({
  value,
  format,
  width,
  label,
  variant,
  latency,
}: {
  value: number | null | undefined;
  format: (value: number) => string;
  width: number;
  label: string;
  variant?: 'b';
  latency?: boolean;
}) {
  return (
    <div className={`metric-value${variant === 'b' ? ' b' : ''}`}>
      <div className="metric-value-head">
        <span>配置 {label}{latency ? ' · 平均' : ''}</span>
        {value == null ? (
          <span className="metric-null"><MinusCircle size={11} /> 未标注</span>
        ) : (
          <strong>{format(value)}</strong>
        )}
      </div>
      {value == null ? (
        <div className="metric-bar is-empty" />
      ) : (
        <div className="metric-bar"><span style={{ width: `${width}%` }} /></div>
      )}
    </div>
  );
}

function ConfigSummary({ config, tone }: { config: RetrievalConfig; tone: 'a' | 'b' }) {
  return (
    <div className="run-config-summary">
      <Badge tone={tone === 'a' ? 'indigo' : 'amber'}>{RETRIEVAL_MODE_LABELS[config?.mode] ?? config?.mode ?? '—'}</Badge>
      <span>topK <code>{config?.topK ?? '—'}</code></span>
      <span>阈值 <code>{config?.threshold ?? '—'}</code></span>
      <span>重排 <code>{config?.rerankModelId ? '已配置' : '未启用'}</code></span>
      <span>改写 <code>{config?.rewriteModelId ? '已配置' : '未启用'}</code></span>
    </div>
  );
}

function ResultDetailModal({
  result,
  hasB,
  reviewEnabled,
  onClose,
  onSaved,
}: {
  result: EvalResult | null;
  hasB: boolean;
  reviewEnabled: boolean;
  onClose: () => void;
  onSaved: () => void;
}) {
  return (
    <Modal open={result != null} onClose={onClose} title="评测结果详情" size="xl" footer={<Button variant="secondary" onClick={onClose}>关闭</Button>}>
      {result && (
        <div className="form-stack">
          <div className="feedback-block">
            <span className="feedback-block-label">问题</span>
            <p>{result.question}</p>
          </div>
          <div className="feedback-block">
            <span className="feedback-block-label">标准答案（业务参考）</span>
            <p className="pre-wrap">{result.expectedAnswer}</p>
          </div>
          <div className="result-modal-answers">
            <div className="result-answer-block">
              <h4>配置 A 回答</h4>
              <p>{result.answerA || '（无回答）'}</p>
              <ResultScores scores={result.scoresA} />
              <ResultCitations citations={result.citationsA} />
              <ReviewForm key={`${result.caseId}-A`} result={result} variant="A" enabled={reviewEnabled} onSaved={onSaved} />
            </div>
            {hasB && (
              <div className="result-answer-block">
                <h4>配置 B 回答</h4>
                <p>{result.answerB || '（无回答）'}</p>
                <ResultScores scores={result.scoresB} />
                <ResultCitations citations={result.citationsB} />
                {result.answerB != null && (
                  <ReviewForm key={`${result.caseId}-B`} result={result} variant="B" enabled={reviewEnabled} onSaved={onSaved} />
                )}
              </div>
            )}
          </div>
          <Callout tone="warn">
            词项匹配与引用评分是自动参考值；人工评审用于记录业务判断，两者分开展示，不会互相覆盖。
          </Callout>
        </div>
      )}
    </Modal>
  );
}

function HumanMetricCard({
  label,
  metrics,
  fallbackTotal,
  variant = 'a',
}: {
  label: string;
  metrics?: HumanMetrics | null;
  fallbackTotal: number;
  variant?: 'a' | 'b';
}) {
  const reviewed = metrics?.reviewed ?? 0;
  const total = metrics?.total ?? fallbackTotal;
  const percent = total > 0 ? (reviewed / total) * 100 : 0;
  return (
    <div className={`human-metric ${variant === 'b' ? 'is-b' : ''}`}>
      <div className="human-metric-head">
        <strong>{label}</strong>
        <Badge tone={reviewed > 0 ? 'indigo' : 'neutral'}>
          已评审 {formatNumber(reviewed)} / {formatNumber(total)}
        </Badge>
      </div>
      <div className="human-metric-progress">
        <ProgressBar value={percent} tone={variant === 'b' ? 'amber' : 'indigo'} label={`${Math.round(percent)}%`} />
      </div>
      <div className="human-metric-values">
        <div>
          <span>答案正确率</span>
          {metrics?.answerAccuracy == null ? (
            <em className="metric-null">未评审</em>
          ) : (
            <strong>{formatPercent(metrics.answerAccuracy)}</strong>
          )}
        </div>
        <div>
          <span>引用正确率</span>
          {metrics?.citationAccuracy == null ? (
            <em className="metric-null">未评审</em>
          ) : (
            <strong>{formatPercent(metrics.citationAccuracy)}</strong>
          )}
        </div>
      </div>
      {reviewed === 0 && <p className="muted-text">尚无业务评审记录；自动指标仍保留在上方。</p>}
    </div>
  );
}

function ReviewForm({
  result,
  variant,
  enabled,
  onSaved,
}: {
  result: EvalResult;
  variant: 'A' | 'B';
  enabled: boolean;
  onSaved: () => void;
}) {
  const toast = useToast();
  const review: EvalReview | null = result.reviews?.[variant] ?? null;
  const [answerCorrect, setAnswerCorrect] = useState<boolean | null>(review?.answerCorrect ?? null);
  const [citationsCorrect, setCitationsCorrect] = useState<boolean | null>(review?.citationsCorrect ?? null);
  const [comment, setComment] = useState(review?.comment ?? '');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const canSave = Boolean(result.id) && answerCorrect != null && citationsCorrect != null && !saving;

  const save = async () => {
    if (!result.id) {
      setError(new Error('该结果缺少 ID，无法保存人工评审。'));
      return;
    }
    if (answerCorrect == null || citationsCorrect == null) {
      setError(new Error('请分别判断答案与引用是否正确。'));
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await evalApi.reviewResult(result.id, {
        variant,
        answerCorrect,
        citationsCorrect,
        comment: comment.trim() || undefined,
      });
      toast.success(`配置 ${variant} 的人工评审已保存`);
      onSaved();
    } catch (err) {
      setError(err);
    } finally {
      setSaving(false);
    }
  };

  if (!enabled) {
    return (
      <div className="review-form">
        <div className="review-form-title">业务评审 · 配置 {variant}</div>
        <p className="muted-text">运行完成后才能进行人工评审。当前状态：{result.id ? '未完成' : '结果缺失'}。</p>
      </div>
    );
  }

  return (
    <div className="review-form">
      <div className="review-form-title">业务评审 · 配置 {variant}</div>
      <div className="review-question">
        <span>答案正确？</span>
        <div className="review-choice-group">
          <button type="button" className={`review-choice${answerCorrect === true ? ' is-yes' : ''}`} onClick={() => setAnswerCorrect(true)}>是</button>
          <button type="button" className={`review-choice${answerCorrect === false ? ' is-no' : ''}`} onClick={() => setAnswerCorrect(false)}>否</button>
        </div>
      </div>
      <div className="review-question">
        <span>引用正确？</span>
        <div className="review-choice-group">
          <button type="button" className={`review-choice${citationsCorrect === true ? ' is-yes' : ''}`} onClick={() => setCitationsCorrect(true)}>是</button>
          <button type="button" className={`review-choice${citationsCorrect === false ? ' is-no' : ''}`} onClick={() => setCitationsCorrect(false)}>否</button>
        </div>
      </div>
      <Textarea
        rows={2}
        value={comment}
        onChange={(event) => setComment(event.target.value)}
        placeholder="评审备注（可选）"
        aria-label={`配置 ${variant} 评审备注`}
      />
      {review?.reviewedAt && (
        <div className="review-meta">
          最近评审：{review.reviewerName || '业务评审人'} · {formatDateTime(review.reviewedAt)}
        </div>
      )}
      {error != null && <div className="inline-error">{error instanceof Error ? error.message : '保存失败'}</div>}
      <div className="review-actions">
        <Button size="sm" variant="primary" loading={saving} disabled={!canSave} onClick={save}>
          保存评审
        </Button>
        <span className="muted-text">保存后刷新人工指标。</span>
      </div>
    </div>
  );
}

function ResultScores({ scores }: { scores?: Record<string, unknown> | null }) {
  if (!scores) return <div className="result-scores"><span>评分：未标注</span></div>;
  const entries: Array<[string, unknown]> = [
    ['答案匹配', scores.answerMatch],
    ['引用准确', scores.citationAccuracy],
    ['检索召回', scores.retrievalRecall],
    ['延迟', scores.latencyMs],
  ];
  return (
    <div className="result-scores">
      {entries.map(([label, value]) => (
        <span key={label}>
          {label} {value == null ? '未标注' : typeof value === 'number' ? (label === '延迟' ? formatDuration(value) : formatPercent(value)) : String(value)}
        </span>
      ))}
    </div>
  );
}

function ResultCitations({ citations }: { citations?: Citation[] }) {
  if (!citations?.length) return null;
  return (
    <div className="result-citations">
      {citations.map((citation) => (
        <div key={`${citation.chunkId}-${citation.index}`} className="result-citation">
          <span className="result-citation-index">[{citation.index}]</span>
          <span>{citation.documentName} · {citation.headingPath?.join(' › ') || '未提供标题路径'}</span>
        </div>
      ))}
    </div>
  );
}

function metricStatus(metrics?: EvalMetrics | null): string {
  if (!metrics) return '未产出';
  const values = [metrics.answerMatch, metrics.citationAccuracy, metrics.retrievalRecall, metrics.refusalRate];
  const annotated = values.filter((value) => value != null).length;
  if (annotated === 0) return '未标注';
  return `${annotated}/${values.length} 项有标注`;
}

function scoreText(value: unknown): string {
  if (value == null) return '未标注';
  if (typeof value === 'number') return formatPercent(value);
  return String(value);
}
