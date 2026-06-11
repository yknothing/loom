import React from "react";
import { Loader2 } from "lucide-react";

export function Card({ className = "", hover = false, children, ...props }) {
  return (
    <div className={`swiss-card ${hover ? "swiss-card-hover" : ""} ${className}`} {...props}>
      {children}
    </div>
  );
}

export function Button({ variant = "primary", className = "", disabled, children, ...props }) {
  const base =
    "inline-flex items-center justify-center gap-2 px-4 py-2 text-sm font-medium rounded-md transition-colors focus:outline-none focus:ring-2 focus:ring-primary focus:ring-offset-2 disabled:opacity-50 disabled:cursor-not-allowed";
  const variants = {
    primary: "bg-primary text-white hover:bg-primary-hover",
    outline: "border border-line bg-white text-ink hover:border-zinc-400 hover:bg-surface",
    ghost: "text-ink hover:bg-surface",
    danger: "bg-red-600 text-white hover:bg-red-700",
  };
  return (
    <button className={`${base} ${variants[variant]} ${className}`} disabled={disabled} {...props}>
      {children}
    </button>
  );
}

export function Input({ className = "", ...props }) {
  return (
    <input
      className={`w-full px-3 py-2 text-sm border border-line rounded-md bg-white focus:outline-none focus:ring-2 focus:ring-primary focus:border-primary ${className}`}
      {...props}
    />
  );
}

export function Textarea({ className = "", ...props }) {
  return (
    <textarea
      className={`w-full px-3 py-2 text-sm border border-line rounded-md bg-white focus:outline-none focus:ring-2 focus:ring-primary focus:border-primary ${className}`}
      {...props}
    />
  );
}

export function Select({ className = "", children, ...props }) {
  return (
    <select
      className={`w-full px-3 py-2 text-sm border border-line rounded-md bg-white focus:outline-none focus:ring-2 focus:ring-primary ${className}`}
      {...props}
    >
      {children}
    </select>
  );
}

const STATUS_STYLES = {
  done: "bg-green-50 text-green-700 border-green-200",
  pending: "bg-zinc-100 text-zinc-600 border-zinc-200",
  running: "bg-blue-50 text-blue-700 border-blue-200",
  failed: "bg-red-50 text-red-700 border-red-200",
  rejected: "bg-amber-50 text-amber-700 border-amber-200",
};
const STATUS_LABELS = {
  done: "已完成",
  pending: "待处理",
  running: "处理中",
  failed: "失败",
  rejected: "已拒绝",
};

export function StatusBadge({ status }) {
  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 text-xs font-medium border rounded ${
        STATUS_STYLES[status] || STATUS_STYLES.pending
      }`}
      data-testid={`status-badge-${status}`}
    >
      {STATUS_LABELS[status] || status}
    </span>
  );
}

export function Badge({ children, className = "" }) {
  return (
    <span className={`inline-flex items-center px-2 py-0.5 text-xs font-medium border border-line rounded bg-surface text-zinc-700 ${className}`}>
      {children}
    </span>
  );
}

export function StatCard({ label, value, sub, icon: Icon, accent = false, testId }) {
  return (
    <Card className="p-6" hover data-testid={testId}>
      <div className="flex items-start justify-between">
        <div>
          <div className="label-xs mb-2">{label}</div>
          <div className={`text-3xl font-bold tracking-tight ${accent ? "text-primary" : "text-ink"}`}>
            {value}
          </div>
          {sub && <div className="text-xs text-muted mt-1">{sub}</div>}
        </div>
        {Icon && <Icon className="w-5 h-5 text-muted" strokeWidth={1.5} />}
      </div>
    </Card>
  );
}

export function Spinner({ className = "w-5 h-5" }) {
  return <Loader2 className={`animate-spin text-primary ${className}`} />;
}

export function PageHeader({ title, sub, children }) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-4 mb-8">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">{title}</h1>
        {sub && <p className="text-sm text-muted mt-1">{sub}</p>}
      </div>
      {children && <div className="flex items-center gap-3">{children}</div>}
    </div>
  );
}

export function EmptyState({ title, sub }) {
  return (
    <div className="text-center py-16">
      <div className="text-lg font-semibold text-zinc-500">{title}</div>
      {sub && <div className="text-sm text-muted mt-1">{sub}</div>}
    </div>
  );
}
