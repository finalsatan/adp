import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useId,
  useState,
  type ButtonHTMLAttributes,
  type InputHTMLAttributes,
  type ReactNode,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from 'react';
import { createPortal } from 'react-dom';
import {
  AlertCircle,
  AlertTriangle,
  Check,
  CheckCircle2,
  ChevronRight,
  Copy,
  Info,
  Loader2,
  RefreshCw,
  Search,
  X,
} from 'lucide-react';
import { useCopy } from '../hooks';
import { errorMessage } from '../utils/format';
import type { DocumentStatus } from '../api/types';
import { DOCUMENT_STATUS_LABELS } from '../utils/permissions';

// ---------------------------------------------------------------------------
// Brand
// ---------------------------------------------------------------------------

export function BrandMark({ size = 'md' }: { size?: 'sm' | 'md' | 'lg' }) {
  return (
    <span className={`brand-mark brand-mark-${size}`} aria-hidden>
      <span className="brand-mark-core">A</span>
      <span className="brand-mark-dot" />
    </span>
  );
}

// ---------------------------------------------------------------------------
// Buttons
// ---------------------------------------------------------------------------

type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger' | 'quiet';
type ButtonSize = 'sm' | 'md' | 'lg';

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  loading?: boolean;
  icon?: ReactNode;
  iconRight?: ReactNode;
  block?: boolean;
}

export function Button({
  variant = 'secondary',
  size = 'md',
  loading = false,
  icon,
  iconRight,
  block = false,
  className = '',
  children,
  disabled,
  ...rest
}: ButtonProps) {
  return (
    <button
      type="button"
      className={`btn btn-${variant} btn-${size}${block ? ' btn-block' : ''}${className ? ` ${className}` : ''}`}
      disabled={disabled || loading}
      {...rest}
    >
      {loading ? <Loader2 size={size === 'sm' ? 14 : 16} className="spin" aria-hidden /> : icon}
      {children != null && <span>{children}</span>}
      {iconRight}
    </button>
  );
}

export function IconButton({
  label,
  size = 16,
  className = '',
  children,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { label: string; size?: number }) {
  return (
    <button
      type="button"
      className={`icon-btn${className ? ` ${className}` : ''}`}
      aria-label={label}
      title={label}
      {...rest}
    >
      {children ?? <ChevronRight size={size} />}
    </button>
  );
}

export function Spinner({ size = 18, label }: { size?: number; label?: string }) {
  return (
    <span className="spinner" role="status" aria-label={label ?? '加载中'}>
      <Loader2 size={size} className="spin" />
      {label && <span>{label}</span>}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Form controls
// ---------------------------------------------------------------------------

interface FieldProps {
  label?: ReactNode;
  hint?: ReactNode;
  error?: ReactNode;
  required?: boolean;
  htmlFor?: string;
  className?: string;
  children: ReactNode;
}

export function Field({ label, hint, error, required, htmlFor, className = '', children }: FieldProps) {
  return (
    <div className={`field${className ? ` ${className}` : ''}`}>
      {label && (
        <label className="field-label" htmlFor={htmlFor}>
          {label}
          {required && <span className="field-required">*</span>}
        </label>
      )}
      {children}
      {error ? <p className="field-error">{error}</p> : hint ? <p className="field-hint">{hint}</p> : null}
    </div>
  );
}

export function Input({ invalid, className = '', ...rest }: InputHTMLAttributes<HTMLInputElement> & { invalid?: boolean }) {
  return <input className={`input${invalid ? ' input-invalid' : ''}${className ? ` ${className}` : ''}`} {...rest} />;
}

export function Textarea({
  invalid,
  className = '',
  ...rest
}: TextareaHTMLAttributes<HTMLTextAreaElement> & { invalid?: boolean }) {
  return (
    <textarea
      className={`input textarea${invalid ? ' input-invalid' : ''}${className ? ` ${className}` : ''}`}
      {...rest}
    />
  );
}

export function Select({ className = '', children, ...rest }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select className={`input select${className ? ` ${className}` : ''}`} {...rest}>
      {children}
    </select>
  );
}

export function SearchInput({
  value,
  onChange,
  placeholder = '搜索',
  className = '',
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  className?: string;
}) {
  return (
    <div className={`search-input${className ? ` ${className}` : ''}`}>
      <Search size={15} aria-hidden />
      <input
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        aria-label={placeholder}
      />
      {value && (
        <button type="button" onClick={() => onChange('')} aria-label="清空搜索">
          <X size={13} />
        </button>
      )}
    </div>
  );
}

export function Switch({
  checked,
  onChange,
  label,
  disabled,
}: {
  checked: boolean;
  onChange: (checked: boolean) => void;
  label: ReactNode;
  disabled?: boolean;
}) {
  return (
    <label className={`switch-row${disabled ? ' is-disabled' : ''}`}>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        className={`switch${checked ? ' is-on' : ''}`}
        onClick={() => !disabled && onChange(!checked)}
        disabled={disabled}
      >
        <span />
      </button>
      <span className="switch-label">{label}</span>
    </label>
  );
}

// ---------------------------------------------------------------------------
// Display primitives
// ---------------------------------------------------------------------------

type BadgeTone = 'neutral' | 'indigo' | 'green' | 'amber' | 'red' | 'blue' | 'purple';

export function Badge({
  children,
  tone = 'neutral',
  className = '',
  dot = false,
}: {
  children: ReactNode;
  tone?: BadgeTone;
  className?: string;
  dot?: boolean;
}) {
  return (
    <span className={`badge badge-${tone}${className ? ` ${className}` : ''}`}>
      {dot && <span className="badge-dot" />}
      {children}
    </span>
  );
}

const STATUS_TONE: Record<string, BadgeTone> = {
  QUEUED: 'neutral',
  PARSING: 'blue',
  PROCESSING: 'blue',
  READY: 'green',
  FAILED: 'red',
  RUNNING: 'blue',
  COMPLETED: 'green',
  queued: 'neutral',
  running: 'blue',
  completed: 'green',
  failed: 'red',
  ok: 'green',
  success: 'green',
  error: 'red',
  draft: 'amber',
  published: 'green',
};

const GENERIC_STATUS_LABELS: Record<string, string> = {
  QUEUED: '排队中',
  RUNNING: '运行中',
  COMPLETED: '已完成',
  FAILED: '失败',
  draft: '草稿',
  published: '已发布',
  queued: '排队中',
  running: '运行中',
  completed: '已完成',
  failed: '失败',
  ok: '成功',
  success: '成功',
  error: '失败',
  succeeded: '成功',
  cancelled: '已取消',
  canceled: '已取消',
};

export function StatusBadge({ status, label }: { status: string; label?: ReactNode }) {
  const normalized = status.toLowerCase();
  const tone = STATUS_TONE[status] ?? STATUS_TONE[normalized] ?? 'neutral';
  const display =
    label ??
    DOCUMENT_STATUS_LABELS[status as DocumentStatus] ??
    GENERIC_STATUS_LABELS[status] ??
    GENERIC_STATUS_LABELS[normalized] ??
    status;
  return (
    <Badge tone={tone} dot>
      {display}
    </Badge>
  );
}

const VIS_TONE: Record<string, BadgeTone> = {
  private: 'neutral',
  organization: 'indigo',
  platform: 'purple',
};

export function VisibilityBadge({ visibility }: { visibility: string }) {
  const label =
    visibility === 'private' ? '仅自己' : visibility === 'organization' ? '本组织' : '全平台';
  return <Badge tone={VIS_TONE[visibility] ?? 'neutral'}>{label}</Badge>;
}

export function ProgressBar({
  value,
  tone = 'indigo',
  label,
}: {
  value: number;
  tone?: 'indigo' | 'green' | 'red' | 'amber';
  label?: string;
}) {
  const normalized = Math.max(0, Math.min(100, Number.isFinite(value) ? value : 0));
  return (
    <div className="progress-wrap">
      <div className="progress" role="progressbar" aria-valuenow={normalized} aria-valuemin={0} aria-valuemax={100}>
        <span className={`progress-fill progress-${tone}`} style={{ width: `${normalized}%` }} />
      </div>
      <span className="progress-label">{label ?? `${Math.round(normalized)}%`}</span>
    </div>
  );
}

export function Avatar({ name, size = 'md' }: { name: string; size?: 'sm' | 'md' }) {
  const initials = name ? name.trim().slice(0, 2) : '?';
  return <span className={`avatar avatar-${size}`}>{initials}</span>;
}

export function CopyButton({
  value,
  label = '复制',
  size = 'sm',
}: {
  value: string;
  label?: string;
  size?: ButtonSize;
}) {
  const { copied, copy } = useCopy();
  return (
    <Button
      size={size}
      variant="ghost"
      icon={copied ? <Check size={14} /> : <Copy size={14} />}
      onClick={async () => {
        await copy(value);
      }}
    >
      {copied ? '已复制' : label}
    </Button>
  );
}

// ---------------------------------------------------------------------------
// Layout blocks
// ---------------------------------------------------------------------------

export function Card({
  children,
  className = '',
  padded = true,
}: {
  children: ReactNode;
  className?: string;
  padded?: boolean;
}) {
  return <section className={`card${padded ? ' card-padded' : ''}${className ? ` ${className}` : ''}`}>{children}</section>;
}

export function PageHeader({
  eyebrow,
  title,
  description,
  actions,
  back,
}: {
  eyebrow?: ReactNode;
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  back?: ReactNode;
}) {
  return (
    <header className="page-header">
      <div className="page-header-main">
        {back}
        {eyebrow && <div className="page-eyebrow">{eyebrow}</div>}
        <h1>{title}</h1>
        {description && <p className="page-description">{description}</p>}
      </div>
      {actions && <div className="page-actions">{actions}</div>}
    </header>
  );
}

export function SectionHeader({
  title,
  description,
  actions,
}: {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="section-header">
      <div>
        <h2>{title}</h2>
        {description && <p>{description}</p>}
      </div>
      {actions && <div className="section-actions">{actions}</div>}
    </div>
  );
}

export function Tabs<T extends string>({
  tabs,
  value,
  onChange,
}: {
  tabs: Array<{ value: T; label: ReactNode; count?: number }>;
  value: T;
  onChange: (value: T) => void;
}) {
  return (
    <div className="tabs" role="tablist">
      {tabs.map((tab) => (
        <button
          key={tab.value}
          type="button"
          role="tab"
          aria-selected={tab.value === value}
          className={`tab${tab.value === value ? ' is-active' : ''}`}
          onClick={() => onChange(tab.value)}
        >
          {tab.label}
          {tab.count != null && <span className="tab-count">{tab.count}</span>}
        </button>
      ))}
    </div>
  );
}

export function EmptyState({
  icon,
  title,
  description,
  action,
  compact = false,
}: {
  icon?: ReactNode;
  title: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
  compact?: boolean;
}) {
  return (
    <div className={`empty-state${compact ? ' empty-compact' : ''}`}>
      {icon && <div className="empty-icon">{icon}</div>}
      <h3>{title}</h3>
      {description && <p>{description}</p>}
      {action && <div className="empty-action">{action}</div>}
    </div>
  );
}

export function ErrorState({
  error,
  onRetry,
  compact = false,
}: {
  error: unknown;
  onRetry?: () => void;
  compact?: boolean;
}) {
  return (
    <div className={`error-state${compact ? ' error-compact' : ''}`} role="alert">
      <AlertCircle size={compact ? 18 : 24} />
      <div>
        <h3>加载失败</h3>
        <p>{errorMessage(error)}</p>
      </div>
      {onRetry && (
        <Button size="sm" variant="secondary" icon={<RefreshCw size={14} />} onClick={onRetry}>
          重试
        </Button>
      )}
    </div>
  );
}

export function LoadingState({ label = '正在加载…' }: { label?: string }) {
  return (
    <div className="loading-state">
      <Spinner size={22} />
      <span>{label}</span>
    </div>
  );
}

export function SkeletonRows({ rows = 5 }: { rows?: number }) {
  return (
    <div className="skeleton-rows" aria-label="正在加载">
      {Array.from({ length: rows }).map((_, index) => (
        <div key={index} className="skeleton-row" />
      ))}
    </div>
  );
}

export function Callout({
  tone = 'info',
  title,
  children,
  action,
}: {
  tone?: 'info' | 'warn' | 'danger' | 'success';
  title?: ReactNode;
  children: ReactNode;
  action?: ReactNode;
}) {
  const Icon = tone === 'warn' ? AlertTriangle : tone === 'danger' ? AlertCircle : tone === 'success' ? CheckCircle2 : Info;
  return (
    <div className={`callout callout-${tone}`}>
      <Icon size={17} aria-hidden />
      <div className="callout-body">
        {title && <strong>{title}</strong>}
        <div>{children}</div>
      </div>
      {action && <div className="callout-action">{action}</div>}
    </div>
  );
}

export function JsonBlock({ value, maxHeight = 280 }: { value: unknown; maxHeight?: number }) {
  const text = typeof value === 'string' ? value : JSON.stringify(value, null, 2);
  return (
    <div className="json-block">
      <pre style={{ maxHeight }}>{text}</pre>
      <CopyButton value={text} />
    </div>
  );
}

export function DescriptionList({
  items,
  columns = 2,
}: {
  items: Array<{ label: ReactNode; value: ReactNode }>;
  columns?: 1 | 2 | 3;
}) {
  return (
    <dl className={`description-list cols-${columns}`}>
      {items.map((item, index) => (
        <div key={index} className="description-item">
          <dt>{item.label}</dt>
          <dd>{item.value}</dd>
        </div>
      ))}
    </dl>
  );
}

export function PermissionDenied({ permission }: { permission?: string }) {
  return (
    <EmptyState
      icon={<AlertTriangle size={22} />}
      title="没有访问权限"
      description={
        permission
          ? `当前组织角色缺少「${permission}」权限，请联系本组织管理员在角色权限中开通。`
          : '当前组织角色缺少访问此页面的权限，请联系本组织管理员。'
      }
    />
  );
}

// ---------------------------------------------------------------------------
// Modal
// ---------------------------------------------------------------------------

export function Modal({
  open,
  onClose,
  title,
  description,
  children,
  footer,
  size = 'md',
  variant = 'center',
}: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  description?: ReactNode;
  children?: ReactNode;
  footer?: ReactNode;
  size?: 'sm' | 'md' | 'lg' | 'xl';
  variant?: 'center' | 'side';
}) {
  const titleId = useId();

  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKeyDown);
    const previous = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.removeEventListener('keydown', onKeyDown);
      document.body.style.overflow = previous;
    };
  }, [open, onClose]);

  if (!open) return null;

  return createPortal(
    <div className={`modal-layer modal-${variant}`} role="presentation">
      <div className="modal-backdrop" onMouseDown={onClose} />
      <div
        className={`modal modal-${size}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="modal-header">
          <div>
            <h2 id={titleId}>{title}</h2>
            {description && <p>{description}</p>}
          </div>
          <IconButton label="关闭" onClick={onClose}>
            <X size={17} />
          </IconButton>
        </header>
        <div className="modal-body">{children}</div>
        {footer && <footer className="modal-footer">{footer}</footer>}
      </div>
    </div>,
    document.body,
  );
}

// ---------------------------------------------------------------------------
// Confirm dialog
// ---------------------------------------------------------------------------

interface ConfirmOptions {
  title: string;
  description?: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  danger?: boolean;
}

type ConfirmFn = (options: ConfirmOptions) => Promise<boolean>;

const ConfirmContext = createContext<ConfirmFn | null>(null);

export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [pending, setPending] = useState<{ options: ConfirmOptions; resolve: (value: boolean) => void } | null>(null);

  const confirm = useCallback<ConfirmFn>((options) => {
    return new Promise<boolean>((resolve) => {
      setPending({ options, resolve });
    });
  }, []);

  const finish = (value: boolean) => {
    pending?.resolve(value);
    setPending(null);
  };

  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      <Modal
        open={pending != null}
        onClose={() => finish(false)}
        title={pending?.options.title ?? ''}
        size="sm"
        footer={
          <>
            <Button variant="secondary" onClick={() => finish(false)}>
              {pending?.options.cancelLabel ?? '取消'}
            </Button>
            <Button
              variant={pending?.options.danger ? 'danger' : 'primary'}
              onClick={() => finish(true)}
              autoFocus
            >
              {pending?.options.confirmLabel ?? '确认'}
            </Button>
          </>
        }
      >
        {pending?.options.description && <div className="confirm-text">{pending.options.description}</div>}
      </Modal>
    </ConfirmContext.Provider>
  );
}

export function useConfirm(): ConfirmFn {
  const context = useContext(ConfirmContext);
  if (!context) throw new Error('useConfirm must be used inside ConfirmProvider');
  return context;
}
