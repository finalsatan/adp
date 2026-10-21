import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  ArrowUpRight,
  CheckCircle2,
  FlaskConical,
  MessageSquareWarning,
  ThumbsDown,
  ThumbsUp,
} from 'lucide-react';
import type { FeedbackItem } from '../../api/types';
import { appApi, evalApi, feedbackApi } from '../../api/client';
import { useApi, useDocumentTitle } from '../../hooks';
import { useAuth } from '../../contexts/AuthContext';
import { useToast } from '../../contexts/ToastContext';
import {
  Badge,
  Button,
  Callout,
  Card,
  EmptyState,
  ErrorState,
  Field,
  LoadingState,
  Modal,
  PageHeader,
  PermissionDenied,
  SearchInput,
  Select,
  Tabs,
  Textarea,
} from '../../components/ui';
import { errorMessage, formatDateTime, formatNumber, truncate } from '../../utils/format';

type FeedbackFilter = 'all' | 'up' | 'down' | 'corrected';

export function FeedbackPage() {
  useDocumentTitle('用户反馈');
  const { can } = useAuth();
  const toast = useToast();
  const [filter, setFilter] = useState<FeedbackFilter>('all');
  const [search, setSearch] = useState('');
  const [detail, setDetail] = useState<FeedbackItem | null>(null);
  const [addTarget, setAddTarget] = useState<FeedbackItem | null>(null);
  const [setId, setSetId] = useState('');
  const [expectedAnswer, setExpectedAnswer] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<unknown>(null);

  const feedbackState = useApi(() => feedbackApi.list(), []);
  const appsState = useApi(() => appApi.list().catch(() => []), []);
  const evalSetsState = useApi(() => evalApi.sets().catch(() => []), [], {
    enabled: can('eval:manage'),
  });

  const items = feedbackState.data ?? [];
  const apps = appsState.data ?? [];
  const evalSets = evalSetsState.data ?? [];

  const counts = useMemo(
    () => ({
      all: items.length,
      up: items.filter((item) => item.rating === 1).length,
      down: items.filter((item) => item.rating === -1).length,
      corrected: items.filter((item) => Boolean(item.correction)).length,
    }),
    [items],
  );

  const filtered = useMemo(() => {
    const keyword = search.trim().toLowerCase();
    return items.filter((item) => {
      if (filter === 'up' && item.rating !== 1) return false;
      if (filter === 'down' && item.rating !== -1) return false;
      if (filter === 'corrected' && !item.correction) return false;
      if (!keyword) return true;
      return (
        item.question.toLowerCase().includes(keyword) ||
        item.answer.toLowerCase().includes(keyword) ||
        (item.correction ?? '').toLowerCase().includes(keyword)
      );
    });
  }, [items, filter, search]);

  if (!can('observe:read')) {
    return (
      <div className="page">
        <PageHeader title="用户反馈" description="终端用户对回答的点赞、点踩与纠错。" />
        <Card><PermissionDenied permission="observe:read" /></Card>
      </div>
    );
  }

  const appName = (id: string) => apps.find((app) => app.id === id)?.name ?? id;

  const openAdd = (item: FeedbackItem) => {
    setAddTarget(item);
    setSetId(evalSets[0]?.id ?? '');
    setExpectedAnswer('');
    setFormError(null);
  };

  const addToEval = async () => {
    if (!addTarget) return;
    if (!setId) {
      setFormError(new Error('请选择目标评测集。'));
      return;
    }
    setSubmitting(true);
    setFormError(null);
    try {
      await evalApi.fromFeedback(setId, {
        feedbackId: addTarget.id,
        expectedAnswer: expectedAnswer.trim() || undefined,
      });
      toast.success('已加入评测集');
      setAddTarget(null);
      evalSetsState.reload();
    } catch (error) {
      setFormError(error);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="page">
      <PageHeader
        eyebrow="应用观测"
        title="用户反馈"
        description="终端用户对回答的点赞、点踩与纠错。点踩和纠错可加入评测集，形成可复用的问题。"
        actions={
          can('eval:manage') ? (
            <Link to="/evals" className="btn btn-secondary btn-md">
              <FlaskConical size={15} />
              <span>去评测集</span>
            </Link>
          ) : undefined
        }
      />

      <div className="stat-grid stat-grid-compact">
        <div className="stat-card">
          <div className="stat-icon"><MessageSquareWarning size={17} /></div>
          <div className="stat-copy"><span>全部反馈</span><strong>{formatNumber(counts.all)}</strong></div>
        </div>
        <div className="stat-card">
          <div className="stat-icon"><ThumbsUp size={17} /></div>
          <div className="stat-copy"><span>有帮助</span><strong>{formatNumber(counts.up)}</strong></div>
        </div>
        <div className="stat-card">
          <div className="stat-icon"><ThumbsDown size={17} /></div>
          <div className="stat-copy"><span>没帮助</span><strong>{formatNumber(counts.down)}</strong></div>
        </div>
        <div className="stat-card">
          <div className="stat-icon"><CheckCircle2 size={17} /></div>
          <div className="stat-copy"><span>含纠错</span><strong>{formatNumber(counts.corrected)}</strong></div>
        </div>
      </div>

      <div className="toolbar toolbar-wrap">
        <Tabs
          tabs={[
            { value: 'all', label: '全部', count: counts.all },
            { value: 'down', label: '点踩', count: counts.down },
            { value: 'corrected', label: '有纠错', count: counts.corrected },
            { value: 'up', label: '点赞', count: counts.up },
          ]}
          value={filter}
          onChange={setFilter}
        />
        <SearchInput value={search} onChange={setSearch} placeholder="搜索问题、回答或纠错内容" className="grow-search" />
      </div>

      <Card padded={false}>
        {feedbackState.loading && !feedbackState.data ? (
          <LoadingState label="正在读取反馈…" />
        ) : feedbackState.error ? (
          <div className="card-pad">
            <ErrorState error={feedbackState.error} onRetry={feedbackState.reload} compact />
          </div>
        ) : filtered.length === 0 ? (
          <EmptyState
            icon={<MessageSquareWarning size={23} />}
            title={items.length === 0 ? '还没有用户反馈' : '没有匹配的反馈'}
            description={
              items.length === 0
                ? '终端用户在对话页点赞、点踩或纠错后，会在这里出现。反馈是评测集的重要来源。'
                : '调整筛选或搜索词后再试。'
            }
            action={
              items.length === 0 && can('apps:read') ? (
                <Link to="/apps" className="btn btn-secondary btn-sm">
                  去应用与对话
                </Link>
              ) : undefined
            }
          />
        ) : (
          <div className="table-wrap">
            <table className="table feedback-table">
              <thead>
                <tr>
                  <th>评价</th>
                  <th>问题与回答</th>
                  <th>纠错</th>
                  <th>应用</th>
                  <th>时间</th>
                  <th className="align-right">操作</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((item) => (
                  <tr key={item.id}>
                    <td>
                      {item.rating === 1 ? (
                        <Badge tone="green"><ThumbsUp size={12} /> 有帮助</Badge>
                      ) : (
                        <Badge tone="red"><ThumbsDown size={12} /> 没帮助</Badge>
                      )}
                    </td>
                    <td>
                      <button type="button" className="feedback-question" onClick={() => setDetail(item)}>
                        {truncate(item.question, 80)}
                      </button>
                      <div className="feedback-answer">{truncate(item.answer, 110)}</div>
                    </td>
                    <td className="feedback-correction-cell">
                      {item.correction ? <span>{truncate(item.correction, 70)}</span> : <span className="muted-text">—</span>}
                    </td>
                    <td>
                      <span className="cell-provider">
                        {can('apps:read') ? (
                          <Link to={`/apps/${item.appId}`} className="text-link">
                            {appName(item.appId)}
                          </Link>
                        ) : (
                          appName(item.appId)
                        )}
                      </span>
                    </td>
                    <td className="muted-cell">{formatDateTime(item.createdAt)}</td>
                    <td>
                      <div className="row-actions row-actions-end">
                        <button type="button" className="row-action" onClick={() => setDetail(item)}>
                          查看
                        </button>
                        {can('eval:manage') && (
                          <button type="button" className="row-action" onClick={() => openAdd(item)}>
                            <FlaskConical size={13} /> 加入评测集
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="card-foot-note">
          反馈只来自真实终端会话。评分与纠错正文由用户提交，可能包含个人信息，请按内部规范处理。
        </div>
      </Card>

      <Modal
        open={detail != null}
        onClose={() => setDetail(null)}
        title="反馈详情"
        size="lg"
        footer={
          <>
            {can('eval:manage') && detail && (
              <Button
                variant="primary"
                icon={<FlaskConical size={15} />}
                onClick={() => {
                  openAdd(detail);
                  setDetail(null);
                }}
              >
                加入评测集
              </Button>
            )}
            <Button variant="secondary" onClick={() => setDetail(null)}>关闭</Button>
          </>
        }
      >
        {detail && (
          <div className="feedback-detail">
            <div className="feedback-detail-head">
              {detail.rating === 1 ? <Badge tone="green">有帮助</Badge> : <Badge tone="red">没帮助</Badge>}
              <span>{formatDateTime(detail.createdAt)}</span>
              <span>应用：{appName(detail.appId)}</span>
            </div>
            <div className="feedback-block">
              <span className="feedback-block-label">用户问题</span>
              <p>{detail.question}</p>
            </div>
            <div className="feedback-block">
              <span className="feedback-block-label">系统回答</span>
              <p className="pre-wrap">{detail.answer}</p>
            </div>
            {detail.correction && (
              <div className="feedback-block feedback-block-correction">
                <span className="feedback-block-label">用户纠错</span>
                <p className="pre-wrap">{detail.correction}</p>
              </div>
            )}
            <div className="feedback-ids">
              <span>反馈 ID <code>{detail.id}</code></span>
              <span>消息 ID <code>{detail.messageId}</code></span>
            </div>
          </div>
        )}
      </Modal>

      <Modal
        open={addTarget != null}
        onClose={() => setAddTarget(null)}
        title="从反馈创建评测用例"
        description="问题与参考答案来自用户反馈；可补充业务确认的标准答案后再加入评测集。"
        size="md"
        footer={
          <>
            <Button variant="secondary" onClick={() => setAddTarget(null)}>取消</Button>
            <Button variant="primary" loading={submitting} onClick={addToEval} disabled={!setId}>
              加入评测集
            </Button>
          </>
        }
      >
        {addTarget && (
          <div className="modal-form">
            <div className="eval-from-feedback">
              <span className="feedback-block-label">问题</span>
              <p>{addTarget.question}</p>
              {addTarget.correction && (
                <>
                  <span className="feedback-block-label">用户纠错</span>
                  <p className="pre-wrap">{addTarget.correction}</p>
                </>
              )}
            </div>
            <Field label="目标评测集" htmlFor="feedback-evalset" required>
              <Select id="feedback-evalset" value={setId} onChange={(event) => setSetId(event.target.value)}>
                {evalSets.length === 0 && <option value="">暂无评测集，请先创建</option>}
                {evalSets.map((set) => (
                  <option key={set.id} value={set.id}>
                    {set.name} · {set.caseCount} 条
                  </option>
                ))}
              </Select>
            </Field>
            <Field
              label="标准答案（可选）"
              htmlFor="feedback-expected"
              hint="未填写时保留服务端默认值；建议由业务确认后再运行评测。"
            >
              <Textarea
                id="feedback-expected"
                rows={4}
                value={expectedAnswer}
                onChange={(event) => setExpectedAnswer(event.target.value)}
                placeholder="填写经业务确认的标准答案"
              />
            </Field>
            {evalSets.length === 0 && (
              <Callout
                tone="warn"
                title="还没有评测集"
                action={
                  <Link to="/evals" className="btn btn-secondary btn-sm">
                    去创建 <ArrowUpRight size={13} />
                  </Link>
                }
              >
                先创建评测集，再把点踩或纠错转为用例。
              </Callout>
            )}
            {formError != null && <Callout tone="danger" title="加入失败">{errorMessage(formError)}</Callout>}
          </div>
        )}
      </Modal>
    </div>
  );
}
