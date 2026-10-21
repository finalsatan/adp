import { useEffect, useState, type FormEvent } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { ArrowRight, Eye, EyeOff, Loader2 } from 'lucide-react';
import { useAuth } from '../../contexts/AuthContext';
import { Button, Callout, Input, Field } from '../../components/ui';
import { errorMessage } from '../../utils/format';
import { AuthLayout } from './AuthLayout';
import { useDocumentTitle } from '../../hooks';

export function SetupPage() {
  useDocumentTitle('初始化管理员');
  const { initialized, user, setup } = useAuth();
  const navigate = useNavigate();
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [orgName, setOrgName] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    if (user) navigate('/', { replace: true });
  }, [user, navigate]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    if (password.length < 8) {
      setError(new Error('密码至少需要 8 个字符。'));
      return;
    }
    if (password !== confirm) {
      setError(new Error('两次输入的密码不一致。'));
      return;
    }
    setSubmitting(true);
    try {
      await setup({ name: name.trim(), email: email.trim(), password, orgName: orgName.trim() });
      navigate('/', { replace: true });
    } catch (err) {
      setError(err);
    } finally {
      setSubmitting(false);
    }
  };

  if (initialized && !user) {
    return (
      <AuthLayout
        title="系统已完成初始化"
        description="该平台已经创建了第一位管理员，请直接登录。"
        footer={
          <Link to="/login" className="text-link">
            前往登录 <ArrowRight size={14} />
          </Link>
        }
      >
        <Callout tone="info">
          初始化入口仅对全新数据目录开放。如果你无法登录，请联系已有系统管理员。
        </Callout>
      </AuthLayout>
    );
  }

  return (
    <AuthLayout
      title="初始化平台管理员"
      description="创建第一位系统管理员与默认组织。完成后即可进入知识库。"
      footer={
        <span>
          已经初始化？{' '}
          <Link to="/login" className="text-link">
            去登录
          </Link>
        </span>
      }
    >
      <form className="auth-form" onSubmit={submit}>
        <div className="form-grid two">
          <Field label="管理员姓名" htmlFor="setup-name" required>
            <Input
              id="setup-name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="例如：张工"
              autoComplete="name"
              autoFocus
              required
            />
          </Field>
          <Field label="默认组织名称" htmlFor="setup-org" required hint="可稍后在系统管理中调整组织树。">
            <Input
              id="setup-org"
              value={orgName}
              onChange={(event) => setOrgName(event.target.value)}
              placeholder="例如：总部"
              required
            />
          </Field>
        </div>
        <Field label="登录邮箱" htmlFor="setup-email" required>
          <Input
            id="setup-email"
            type="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            placeholder="name@company.com"
            autoComplete="email"
            required
          />
        </Field>
        <div className="form-grid two">
          <Field label="密码" htmlFor="setup-password" required hint="至少 8 个字符。">
            <div className="password-wrap">
              <Input
                id="setup-password"
                type={showPassword ? 'text' : 'password'}
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                autoComplete="new-password"
                minLength={8}
                required
              />
              <button
                type="button"
                className="password-toggle"
                aria-label={showPassword ? '隐藏密码' : '显示密码'}
                onClick={() => setShowPassword((value) => !value)}
              >
                {showPassword ? <EyeOff size={16} /> : <Eye size={16} />}
              </button>
            </div>
          </Field>
          <Field label="确认密码" htmlFor="setup-confirm" required>
            <Input
              id="setup-confirm"
              type={showPassword ? 'text' : 'password'}
              value={confirm}
              onChange={(event) => setConfirm(event.target.value)}
              autoComplete="new-password"
              required
            />
          </Field>
        </div>

        {error != null && (
          <Callout tone="danger" title="无法完成初始化">
            {errorMessage(error)}
          </Callout>
        )}

        <Button type="submit" variant="primary" size="lg" block loading={submitting} iconRight={<ArrowRight size={16} />}>
          创建管理员并进入
        </Button>
        <p className="auth-legal">
          初始化会写入本地数据库。管理员邮箱与密码用于后续登录，请妥善保管。
        </p>
      </form>
    </AuthLayout>
  );
}

export function LoginPage() {
  useDocumentTitle('登录');
  const { initialized, user, login, oidcEnabled } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const redirect = (() => {
    const params = new URLSearchParams(window.location.search);
    const value = params.get('redirect');
    if (value && value.startsWith('/') && !value.startsWith('//')) return value;
    return '/';
  })();

  useEffect(() => {
    if (user) navigate(redirect, { replace: true });
  }, [user, navigate, redirect]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await login(email.trim(), password);
      navigate(redirect, { replace: true });
    } catch (err) {
      setError(err);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <AuthLayout
      title="登录工作台"
      description="使用公司账号登录，进入当前组织的知识库与应用。"
      footer={
        <span>
          还没有账号？请联系系统管理员创建。{' '}
          {!initialized && (
            <Link to="/setup" className="text-link">
              首次初始化为管理员
            </Link>
          )}
        </span>
      }
    >
      {!initialized && (
        <Callout tone="warn" title="平台尚未初始化">
          当前数据目录中还没有管理员。请先完成首次初始化，再使用登录。{' '}
          <Link to="/setup" className="text-link">
            去初始化
          </Link>
        </Callout>
      )}
      <form className="auth-form" onSubmit={submit}>
        <Field label="邮箱" htmlFor="login-email" required>
          <Input
            id="login-email"
            type="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            placeholder="name@company.com"
            autoComplete="username"
            autoFocus
            required
          />
        </Field>
        <Field label="密码" htmlFor="login-password" required>
          <div className="password-wrap">
            <Input
              id="login-password"
              type={showPassword ? 'text' : 'password'}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              autoComplete="current-password"
              required
            />
            <button
              type="button"
              className="password-toggle"
              aria-label={showPassword ? '隐藏密码' : '显示密码'}
              onClick={() => setShowPassword((value) => !value)}
            >
              {showPassword ? <EyeOff size={16} /> : <Eye size={16} />}
            </button>
          </div>
        </Field>

        {error != null && (
          <Callout tone="danger" title="登录失败">
            {errorMessage(error)}
          </Callout>
        )}

        <Button type="submit" variant="primary" size="lg" block loading={submitting} iconRight={<ArrowRight size={16} />}>
          登录
        </Button>

        {oidcEnabled && (
          <>
            <div className="auth-or">
              <span>或</span>
            </div>
            <a className="btn btn-secondary btn-lg btn-block" href="/api/auth/oidc/start">
              <Loader2 size={16} className="oidc-icon" aria-hidden />
              <span>使用企业 SSO 登录</span>
            </a>
          </>
        )}
      </form>
    </AuthLayout>
  );
}
