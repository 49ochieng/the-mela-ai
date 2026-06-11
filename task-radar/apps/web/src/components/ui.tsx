/**
 * UI primitives — small, dependency-free building blocks used across the app.
 * Keep them visually consistent with the design tokens in tailwind.config.ts.
 */
import * as React from "react";
import clsx from "clsx";

/* ── Card ─────────────────────────────────────────────────────────────── */
export function Card({
  className,
  padded = true,
  hover = false,
  ...rest
}: React.HTMLAttributes<HTMLDivElement> & { padded?: boolean; hover?: boolean }) {
  return (
    <div
      {...rest}
      className={clsx(
        hover ? "card-hover" : "card",
        padded && "p-5 sm:p-6",
        className,
      )}
    />
  );
}

export function CardHeader({
  title,
  subtitle,
  action,
  className,
}: {
  title: React.ReactNode;
  subtitle?: React.ReactNode;
  action?: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={clsx("flex items-start justify-between gap-4 mb-4", className)}>
      <div>
        <h3 className="text-base font-semibold text-ink tracking-tight">{title}</h3>
        {subtitle && <p className="text-sm text-muted mt-0.5">{subtitle}</p>}
      </div>
      {action}
    </div>
  );
}

/* ── Button ───────────────────────────────────────────────────────────── */
type BtnVariant = "primary" | "ghost" | "subtle" | "danger";
type BtnSize = "sm" | "md" | "lg";

export const Button = React.forwardRef<
  HTMLButtonElement,
  React.ButtonHTMLAttributes<HTMLButtonElement> & {
    variant?: BtnVariant;
    size?: BtnSize;
    leftIcon?: React.ReactNode;
    rightIcon?: React.ReactNode;
  }
>(function Button(
  { variant = "primary", size = "md", leftIcon, rightIcon, className, children, ...rest },
  ref,
) {
  const v =
    variant === "ghost" ? "btn-ghost"
    : variant === "subtle" ? "btn-subtle"
    : variant === "danger" ? "btn-danger"
    : "btn-primary";
  const s =
    size === "sm" ? "px-3 py-1.5 text-xs"
    : size === "lg" ? "px-5 py-3 text-sm"
    : "";
  return (
    <button ref={ref} {...rest} className={clsx(v, s, className)}>
      {leftIcon && <span className="-ml-0.5">{leftIcon}</span>}
      {children}
      {rightIcon && <span className="-mr-0.5">{rightIcon}</span>}
    </button>
  );
});

/* ── Badge / Status ───────────────────────────────────────────────────── */
export function Badge({
  tone = "neutral",
  children,
  className,
}: {
  tone?: "neutral" | "brand" | "success" | "warning" | "danger";
  children: React.ReactNode;
  className?: string;
}) {
  const cls =
    tone === "brand" ? "badge-brand"
    : tone === "success" ? "badge-success"
    : tone === "warning" ? "badge-warning"
    : tone === "danger" ? "badge-danger"
    : "badge-neutral";
  return <span className={clsx(cls, className)}>{children}</span>;
}

export function PriorityBadge({ value }: { value?: string | null }) {
  const v = (value || "").toLowerCase();
  if (v === "high") return <Badge tone="danger">High priority</Badge>;
  if (v === "medium" || v === "med") return <Badge tone="warning">Medium</Badge>;
  if (v === "low") return <Badge tone="success">Low</Badge>;
  return <Badge tone="neutral">Unset</Badge>;
}

export function SourceBadge({ source }: { source?: string | null }) {
  const v = (source || "").toLowerCase();
  const map: Record<string, { label: string; tone: "brand" | "neutral" | "success" }> = {
    outlook:        { label: "Outlook",        tone: "brand"   },
    email:          { label: "Email",          tone: "brand"   },
    teams:          { label: "Teams",          tone: "brand"   },
    teams_channel:  { label: "Teams channel",  tone: "brand"   },
    teams_chat:     { label: "Teams chat",     tone: "brand"   },
    planner:        { label: "Planner",        tone: "neutral" },
    excel:          { label: "Excel",          tone: "success" },
  };
  const m = map[v] || { label: source || "Unknown", tone: "neutral" as const };
  return <Badge tone={m.tone}>{m.label}</Badge>;
}

export function StatusDot({ tone }: { tone: "success" | "warning" | "danger" | "neutral" }) {
  const c =
    tone === "success" ? "bg-success"
    : tone === "warning" ? "bg-warning"
    : tone === "danger" ? "bg-danger"
    : "bg-subtle";
  return <span className={clsx("status-dot", c)} />;
}

export function ConfidenceMeter({ value }: { value?: number | null }) {
  const pct = Math.max(0, Math.min(100, Math.round((value ?? 0) * 100)));
  const tone = pct >= 80 ? "bg-success" : pct >= 50 ? "bg-warning" : "bg-danger";
  return (
    <div className="flex items-center gap-2 min-w-[120px]">
      <div className="flex-1 h-1.5 bg-canvas rounded-full overflow-hidden">
        <div className={clsx("h-full rounded-full", tone)} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-xs text-muted tabular-nums">{pct}%</span>
    </div>
  );
}

/* ── Empty / Loading / Error ──────────────────────────────────────────── */
export function EmptyState({
  icon,
  title,
  description,
  action,
}: {
  icon?: React.ReactNode;
  title: string;
  description?: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center text-center py-12 px-6">
      {icon && (
        <div className="w-12 h-12 rounded-2xl bg-brand/10 text-brand flex items-center justify-center mb-4">
          {icon}
        </div>
      )}
      <h3 className="text-base font-semibold text-ink">{title}</h3>
      {description && <p className="text-sm text-muted mt-1.5 max-w-md">{description}</p>}
      {action && <div className="mt-5">{action}</div>}
    </div>
  );
}

export function LoadingState({ rows = 3 }: { rows?: number }) {
  return (
    <div className="space-y-3">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="skeleton h-16 w-full" />
      ))}
    </div>
  );
}

export function ErrorState({
  title = "Something went wrong",
  description,
  onRetry,
}: {
  title?: string;
  description?: string;
  onRetry?: () => void;
}) {
  return (
    <div className="card p-6 border-danger/20 bg-red-50/40 text-center">
      <h3 className="text-sm font-semibold text-danger">{title}</h3>
      {description && <p className="text-xs text-muted mt-1">{description}</p>}
      {onRetry && (
        <button onClick={onRetry} className="btn-ghost text-xs mt-3">
          Try again
        </button>
      )}
    </div>
  );
}

/* ── Page header ──────────────────────────────────────────────────────── */
export function PageHeader({
  eyebrow,
  title,
  description,
  actions,
}: {
  eyebrow?: React.ReactNode;
  title: React.ReactNode;
  description?: React.ReactNode;
  actions?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col sm:flex-row sm:items-end sm:justify-between gap-4 mb-7">
      <div>
        {eyebrow && (
          <div className="text-xs uppercase tracking-wider text-brand font-medium mb-2">
            {eyebrow}
          </div>
        )}
        <h1 className="text-2xl sm:text-[28px] font-semibold tracking-tight text-ink">
          {title}
        </h1>
        {description && <p className="text-sm text-muted mt-2 max-w-2xl">{description}</p>}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  );
}

/* ── MetricCard ───────────────────────────────────────────────────────── */
export function MetricCard({
  label,
  value,
  delta,
  trend,
  icon,
  href,
}: {
  label: string;
  value: React.ReactNode;
  delta?: string;
  trend?: "up" | "down" | "flat";
  icon?: React.ReactNode;
  href?: string;
}) {
  const Wrap: any = href ? "a" : "div";
  const trendColor =
    trend === "up" ? "text-success"
    : trend === "down" ? "text-danger"
    : "text-muted";
  return (
    <Wrap
      href={href}
      className={clsx(
        "card p-5 block transition-all",
        href && "hover:shadow-card hover:-translate-y-[1px]",
      )}
    >
      <div className="flex items-center justify-between">
        <span className="text-sm text-muted">{label}</span>
        {icon && (
          <span className="w-9 h-9 rounded-xl bg-brand/8 text-brand flex items-center justify-center">
            {icon}
          </span>
        )}
      </div>
      <div className="mt-3 text-3xl font-semibold tracking-tight text-ink tabular-nums">
        {value}
      </div>
      {delta && <div className={clsx("text-xs mt-1.5", trendColor)}>{delta}</div>}
    </Wrap>
  );
}

/* ── Filter pills ─────────────────────────────────────────────────────── */
export function FilterPills<T extends string>({
  options,
  value,
  onChange,
}: {
  options: { value: T; label: string; count?: number }[];
  value: T;
  onChange: (v: T) => void;
}) {
  return (
    <div className="flex flex-wrap gap-2">
      {options.map((o) => (
        <button
          key={o.value}
          onClick={() => onChange(o.value)}
          className={value === o.value ? "pill-active" : "pill"}
          type="button"
        >
          {o.label}
          {typeof o.count === "number" && (
            <span className="text-xs text-muted ml-1 tabular-nums">{o.count}</span>
          )}
        </button>
      ))}
    </div>
  );
}
