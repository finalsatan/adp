import { useMemo, useState, type FormEvent } from 'react';
import { ShieldCheck, UserPlus, Users, X } from 'lucide-react';
import type { Grant, GrantLevel, KnowledgeBase, MinimalUser } from '../../api/types';
import type { AsyncState } from '../../hooks';
import { knowledgeApi } from '../../api/client';
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
  Select,
  useConfirm,
} from '../../components/ui';
import { GRANT_LEVEL_LABELS } from '../../utils/permissions';
import { errorMessage, formatDateTime, initials } from '../../utils/format';

const LEVEL_TONE: Record<GrantLevel, 'neutral' | 'indigo' | 'purple'> = {
  view: 'neutral',
  edit: 'indigo',
  grant: 'purple',
};

export function KnowledgeGrantsTab({
  kb,
  grantsState,
  usersState,
  canGrant,
  currentUserId,
  onChanged,
}: {
  kb: KnowledgeBase;
  grantsState: AsyncState<Grant[]>;
  usersState: AsyncState<MinimalUser[]>;
  canGrant: boolean;
  currentUserId?: string;
  onChanged: () => void;
}) {
  const toast = useToast();
  const confirm = useConfirm();
  const [addOpen, setAddOpen] = useState(false);
  const [userId, setUserId] = useState('');
  const [level, setLevel] = useState<GrantLevel>('view');
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<unknown>(null);

  const grants = grantsState.data ?? [];
  const users = usersState.data ?? [];
  const grantedIds = useMemo(() => new Set(grants.map((grant) => grant.userId)), [grants]);
  const candidates = useMemo(() => users.filter((user) => !grantedIds.has(user.id)), [users, grantedIds]);
  const userMap = useMemo(() => new Map(users.map((user) => [user.id, user])), [users]);

  const openAdd = () => {
    setUserId(candidates[0]?.id ?? '');
    setLevel('view');
    setFormError(null);
    setAddOpen(true);
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!userId) {
      setFormError(new Error('请选择要授权的成员。'));
      return;
    }
    setSubmitting(true);
    setFormError(null);
    try {
      await knowledgeApi.addGrant(kb.id, { userId, level });
      toast.success(`已授予「${GRANT_LEVEL_LABELS[level]}」权限`);
      setAddOpen(false);
      onChanged();
    } catch (error) {
      setFormError(error);
    } finally {
      setSubmitting(false);
    }
  };

  const remove = async (grant: Grant) => {
    const display = grant.userName ?? grant.name ?? userMap.get(grant.userId)?.name ?? grant.userId;
    if (
      !(await confirm({
        title: '移除授权？',
        description: `移除「${display}」对知识库「${kb.name}」的访问权限。该成员已创建的问答记录不会被删除。`,
        confirmLabel: '移除授权',
        danger: true,
      }))
    ) {
      return;
    }
    try {
      await knowledgeApi.removeGrant(kb.id, grant.userId);
      toast.success('授权已移除');
      onChanged();
    } catch (error) {
      toast.error(errorMessage(error));
    }
  };

  if (!canGrant) {
    return (
      <Card>
        <Callout tone="info" title="缺少授权权限">
          当前角色没有「knowledge:grant」权限，无法查看或修改成员授权。请联系知识库所有者或组织管理员。
        </Callout>
      </Card>
    );
  }

  return (
    <div className="tab-stack">
      <Card padded={false}>
        <div className="card-toolbar">
          <div className="card-toolbar-stat">
            <Users size={16} />
            <span>
              已授权 <strong>{grants.length}</strong> 人
            </span>
            <span className="muted-text">授权仅到知识库级，不细化到单篇文档。</span>
          </div>
          <div className="card-toolbar-actions">
            <Button
              variant="primary"
              icon={<UserPlus size={15} />}
              onClick={openAdd}
              disabled={usersState.loading}
            >
              添加成员
            </Button>
          </div>
        </div>

        {grantsState.loading && !grantsState.data ? (
          <LoadingState label="正在读取授权…" />
        ) : grantsState.error ? (
          <div className="card-pad">
            <ErrorState error={grantsState.error} onRetry={grantsState.reload} compact />
          </div>
        ) : grants.length === 0 ? (
          <EmptyState
            icon={<ShieldCheck size={23} />}
            title="尚未单独授权"
            description="知识库的可见范围由「仅自己 / 本组织 / 全平台」控制；这里可以为特定成员追加查看、编辑或授权权限。"
            action={
              <Button variant="secondary" icon={<UserPlus size={15} />} onClick={openAdd} disabled={candidates.length === 0}>
                添加成员
              </Button>
            }
          />
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>成员</th>
                  <th>权限级别</th>
                  <th>授权时间</th>
                  <th className="align-right">操作</th>
                </tr>
              </thead>
              <tbody>
                {grants.map((grant) => {
                  const member = userMap.get(grant.userId);
                  const name = grant.userName ?? grant.name ?? member?.name ?? grant.userId;
                  const email = grant.userEmail ?? grant.email ?? member?.email ?? '';
                  return (
                    <tr key={grant.userId}>
                      <td>
                        <div className="member-cell">
                          <span className="member-avatar">{initials(name)}</span>
                          <div>
                            <strong>
                              {name}
                              {grant.userId === currentUserId && <span className="self-mark">我</span>}
                            </strong>
                            {email && <span>{email}</span>}
                          </div>
                        </div>
                      </td>
                      <td>
                        <Badge tone={LEVEL_TONE[grant.level]}>{GRANT_LEVEL_LABELS[grant.level] ?? grant.level}</Badge>
                      </td>
                      <td className="muted-cell">{grant.createdAt ? formatDateTime(grant.createdAt) : '—'}</td>
                      <td className="align-right">
                        <Button size="sm" variant="ghost" icon={<X size={14} />} onClick={() => remove(grant)}>
                          移除
                        </Button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        {usersState.error != null && (
          <div className="card-pad">
            <Callout
              tone="warn"
              title="无法读取授权候选人"
              action={
                <Button size="sm" variant="secondary" onClick={usersState.reload}>
                  重试
                </Button>
              }
            >
              授权列表仍可展示，但新增授权需要选择成员。该接口按知识库的实际授权权限校验，不会开放整个系统成员目录。
            </Callout>
          </div>
        )}
        {!usersState.error && !usersState.loading && candidates.length === 0 && users.length > 0 && (
          <div className="card-foot-note">所有可选择的成员都已获得授权。</div>
        )}
      </Card>

      <div className="level-cards">
        <LevelCard
          level="view"
          title="查看"
          description="查看文档、分块与检索测试结果，可在问答中引用该知识库。"
        />
        <LevelCard
          level="edit"
          title="编辑"
          description="在查看基础上，上传、替换、删除文档，编辑分块与配置。"
        />
        <LevelCard
          level="grant"
          title="授权"
          description="在编辑基础上，为其他成员分配此知识库的三档权限。"
        />
      </div>

      <Modal
        open={addOpen}
        onClose={() => setAddOpen(false)}
        title="添加知识库成员"
        description={`为「${kb.name}」单独授权，不会改变知识库的整体可见范围。`}
        size="md"
        footer={
          <>
            <Button variant="secondary" onClick={() => setAddOpen(false)}>
              取消
            </Button>
            <Button variant="primary" loading={submitting} onClick={submit} disabled={!userId}>
              授予权限
            </Button>
          </>
        }
      >
        <form className="modal-form" onSubmit={submit}>
          <Field label="成员" htmlFor="grant-user" required hint={`可选 ${candidates.length} 人，已授权成员不会重复出现。`}>
            <Select
              id="grant-user"
              value={userId}
              onChange={(event) => setUserId(event.target.value)}
              disabled={candidates.length === 0}
            >
              {candidates.length === 0 && <option value="">暂无可授权成员</option>}
              {candidates.map((user) => (
                <option key={user.id} value={user.id}>
                  {user.name} · {user.email}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="权限级别" htmlFor="grant-level" hint="授权可在之后调整或移除。">
            <Select
              id="grant-level"
              value={level}
              onChange={(event) => setLevel(event.target.value as GrantLevel)}
            >
              <option value="view">查看</option>
              <option value="edit">编辑</option>
              <option value="grant">授权</option>
            </Select>
          </Field>
          {formError != null && <Callout tone="danger" title="授权失败">{errorMessage(formError)}</Callout>}
        </form>
      </Modal>
    </div>
  );
}

function LevelCard({ level, title, description }: { level: GrantLevel; title: string; description: string }) {
  return (
    <Card className="level-card">
      <div className="level-card-head">
        <Badge tone={LEVEL_TONE[level]}>{title}</Badge>
        <span className="level-code">{level}</span>
      </div>
      <p>{description}</p>
    </Card>
  );
}
