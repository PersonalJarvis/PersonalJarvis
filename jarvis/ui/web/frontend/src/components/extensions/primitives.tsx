/**
 * Shared building blocks for the "Skills, Plugins & MCPs" section.
 *
 * The section follows one quiet pattern across its three views — a title row
 * with a few actions on the right, a plain table underneath, and a detail page
 * that replaces the table with a "← Back" link at the top. These primitives
 * carry that pattern so the three views cannot drift apart visually: the same
 * row height, the same hairlines, the same muted header, the same menu.
 *
 * Everything here is theme-token based (border-border, bg-card, text-muted-
 * foreground …) so it reads the same in light and dark mode.
 */
import {
  type ReactNode,
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
} from "react";
import { ArrowLeft, ChevronDown, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Panel header — title + right-aligned actions
// ---------------------------------------------------------------------------

export function PanelHeader({
  title,
  subtitle,
  actions,
  className,
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex items-start justify-between gap-4", className)}>
      <div className="min-w-0">
        <h2 className="font-display text-base font-semibold tracking-tight text-foreground">
          {title}
        </h2>
        {subtitle ? (
          <p className="mt-0.5 text-xs text-muted-foreground">{subtitle}</p>
        ) : null}
      </div>
      {actions ? (
        <div className="flex shrink-0 items-center gap-1">{actions}</div>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Icon button — the small round-cornered buttons in the header row
// ---------------------------------------------------------------------------

export function IconButton({
  label,
  onClick,
  children,
  active,
  disabled,
  busy,
  className,
}: {
  label: string;
  onClick?: () => void;
  children: ReactNode;
  active?: boolean;
  disabled?: boolean;
  busy?: boolean;
  className?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled || busy}
      aria-label={label}
      aria-pressed={active}
      title={label}
      className={cn(
        "grid h-8 w-8 place-items-center rounded-md text-muted-foreground transition-colors",
        "hover:bg-accent hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50",
        active && "bg-accent text-foreground",
        className,
      )}
    >
      {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : children}
    </button>
  );
}

// ---------------------------------------------------------------------------
// Soft button — the quiet "Browse" / "Add ▾" pills in the header row
// ---------------------------------------------------------------------------

export function SoftButton({
  children,
  onClick,
  disabled,
  primary,
  className,
  ariaLabel,
  ariaExpanded,
  ariaHasPopup,
}: {
  children: ReactNode;
  onClick?: () => void;
  disabled?: boolean;
  primary?: boolean;
  className?: string;
  ariaLabel?: string;
  ariaExpanded?: boolean;
  ariaHasPopup?: boolean | "menu";
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-label={ariaLabel}
      aria-expanded={ariaExpanded}
      aria-haspopup={ariaHasPopup}
      className={cn(
        "inline-flex h-8 items-center gap-1.5 rounded-md px-3 text-xs font-medium transition-colors",
        "disabled:cursor-not-allowed disabled:opacity-50",
        primary
          ? "bg-primary text-primary-foreground hover:bg-primary/90"
          : "bg-sheen/[0.06] text-foreground hover:bg-sheen/[0.12]",
        className,
      )}
    >
      {children}
    </button>
  );
}

// ---------------------------------------------------------------------------
// Action menu — a button that opens a small list of actions
// ---------------------------------------------------------------------------

export interface MenuAction {
  id: string;
  label: string;
  icon?: ReactNode;
  onSelect: () => void;
  disabled?: boolean;
  destructive?: boolean;
  /** A thin rule above this entry, for grouping. */
  separatorAbove?: boolean;
}

/**
 * Dropdown menu anchored to its trigger. Dismisses on outside click and Escape.
 * `trigger` receives the open state so the caller can render any button shape
 * (the "Add ▾" pill or a bare "⋯" icon).
 */
export function ActionMenu({
  actions,
  trigger,
  align = "end",
  label,
}: {
  actions: MenuAction[];
  trigger: (opts: { open: boolean; toggle: () => void }) => ReactNode;
  align?: "start" | "end";
  label: string;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const menuId = useId();

  const close = useCallback(() => setOpen(false), []);
  const toggle = useCallback(() => setOpen((v) => !v), []);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) close();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, close]);

  return (
    <div ref={rootRef} className="relative">
      {trigger({ open, toggle })}
      {open && (
        <div
          id={menuId}
          role="menu"
          aria-label={label}
          className={cn(
            "absolute z-40 mt-1 min-w-[200px] overflow-hidden rounded-lg border border-border bg-popover p-1 text-popover-foreground shadow-lg",
            align === "end" ? "right-0" : "left-0",
          )}
        >
          {actions.map((a) => (
            <div key={a.id}>
              {a.separatorAbove && <div className="my-1 h-px bg-border" />}
              <button
                type="button"
                role="menuitem"
                disabled={a.disabled}
                onClick={() => {
                  close();
                  a.onSelect();
                }}
                className={cn(
                  "flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-left text-xs transition-colors",
                  "hover:bg-accent disabled:cursor-not-allowed disabled:opacity-50",
                  a.destructive && "text-destructive hover:bg-destructive/10",
                )}
              >
                {a.icon ? (
                  <span className="grid h-4 w-4 place-items-center text-muted-foreground">
                    {a.icon}
                  </span>
                ) : null}
                <span className="flex-1 truncate">{a.label}</span>
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/** The "Add ▾" pill — the most common trigger shape for `ActionMenu`. */
export function MenuPill({
  children,
  open,
  toggle,
  primary,
}: {
  children: ReactNode;
  open: boolean;
  toggle: () => void;
  primary?: boolean;
}) {
  return (
    <SoftButton onClick={toggle} primary={primary} ariaExpanded={open} ariaHasPopup="menu">
      {children}
      <ChevronDown className={cn("h-3.5 w-3.5 transition-transform", open && "rotate-180")} />
    </SoftButton>
  );
}

// ---------------------------------------------------------------------------
// Table — header row, body rows, cells
// ---------------------------------------------------------------------------

export function Table({ children, className, label }: { children: ReactNode; className?: string; label?: string }) {
  return (
    <div role="table" aria-label={label} className={cn("w-full text-sm", className)}>
      {children}
    </div>
  );
}

/** Column spec for `TableHead` + `TableRow`; `width` is any CSS grid track. */
export interface Column {
  id: string;
  label: ReactNode;
  width?: string;
  align?: "left" | "right" | "center";
  /** Screen-reader only header (e.g. the toggle column). */
  srOnly?: boolean;
}

export function gridTemplate(columns: Column[]): string {
  return columns.map((c) => c.width ?? "minmax(0, 1fr)").join(" ");
}

export function TableHead({ columns }: { columns: Column[] }) {
  return (
    <div
      role="row"
      className="grid items-center gap-x-5 border-b border-border px-3 py-2.5 text-[13px] text-muted-foreground"
      style={{ gridTemplateColumns: gridTemplate(columns) }}
    >
      {columns.map((c) => (
        <div
          key={c.id}
          role="columnheader"
          className={cn(
            "truncate",
            c.align === "right" && "text-right",
            c.align === "center" && "text-center",
          )}
        >
          {/* The label stays for assistive tech, but the cell keeps its grid
              track — an absolutely positioned header would shift every
              header after it one column to the left. */}
          {c.srOnly ? <span className="sr-only">{c.label}</span> : c.label}
        </div>
      ))}
    </div>
  );
}

export function TableRow({
  columns,
  children,
  onClick,
  id,
  className,
  selected,
  ariaLabel,
}: {
  columns: Column[];
  children: ReactNode;
  onClick?: () => void;
  id?: string;
  className?: string;
  selected?: boolean;
  ariaLabel?: string;
}) {
  const clickable = typeof onClick === "function";
  return (
    <div
      id={id}
      role="row"
      aria-label={ariaLabel}
      aria-selected={selected}
      tabIndex={clickable ? 0 : undefined}
      onClick={onClick}
      onKeyDown={
        clickable
          ? (e) => {
              if (e.target !== e.currentTarget) return;
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                onClick?.();
              }
            }
          : undefined
      }
      className={cn(
        "group grid min-h-[54px] items-center gap-x-5 border-b border-border/70 px-3 py-2.5 transition-colors last:border-b-0",
        clickable &&
          "cursor-pointer hover:bg-sheen/[0.05] focus:outline-none focus-visible:bg-sheen/[0.06]",
        selected && "bg-sheen/[0.06]",
        className,
      )}
      style={{ gridTemplateColumns: gridTemplate(columns) }}
    >
      {children}
    </div>
  );
}

export function Cell({
  children,
  align,
  className,
  muted,
  stop,
}: {
  children?: ReactNode;
  align?: "left" | "right" | "center";
  className?: string;
  muted?: boolean;
  /** Clicks inside this cell must not open the row (switches, buttons). */
  stop?: boolean;
}) {
  return (
    <div
      role="cell"
      onClick={stop ? (e) => e.stopPropagation() : undefined}
      onKeyDown={stop ? (e) => e.stopPropagation() : undefined}
      className={cn(
        "min-w-0",
        align === "right" && "flex justify-end text-right",
        align === "center" && "flex justify-center text-center",
        muted && "text-sm text-muted-foreground",
        className,
      )}
    >
      {children}
    </div>
  );
}

/** The empty-state block under a table: short, centred, dashed frame. */
export function EmptyRow({ children }: { children: ReactNode }) {
  return (
    <div className="rounded-lg border border-dashed border-border px-6 py-10 text-center text-sm text-muted-foreground">
      {children}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Detail page scaffolding
// ---------------------------------------------------------------------------

export function BackLink({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="inline-flex items-center gap-1.5 text-sm font-medium text-foreground/90 transition-colors hover:text-foreground"
    >
      <ArrowLeft className="h-4 w-4" />
      {label}
    </button>
  );
}

export function DetailHeader({
  leading,
  title,
  titleAccessory,
  byline,
  actions,
}: {
  leading?: ReactNode;
  title: ReactNode;
  titleAccessory?: ReactNode;
  byline?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="flex items-start justify-between gap-4">
      <div className="flex min-w-0 items-start gap-3">
        {leading}
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <h3 className="truncate text-xl font-semibold leading-tight">{title}</h3>
            {titleAccessory}
          </div>
          {byline ? <p className="mt-1 text-sm text-muted-foreground">{byline}</p> : null}
        </div>
      </div>
      {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
    </div>
  );
}

/** A description that clamps to two lines with a "See more" toggle. */
export function ClampedText({
  text,
  moreLabel,
  lessLabel,
  className,
}: {
  text: string;
  moreLabel: string;
  lessLabel: string;
  className?: string;
}) {
  const [expanded, setExpanded] = useState(false);
  const [overflows, setOverflows] = useState(false);
  const ref = useRef<HTMLParagraphElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    // Measure once clamped; a text that fits needs no toggle.
    setOverflows(el.scrollHeight > el.clientHeight + 1);
  }, [text]);

  if (!text) return null;
  return (
    <div className={cn("text-[15px] leading-relaxed text-foreground/85", className)}>
      <p ref={ref} className={cn(!expanded && "line-clamp-2")}>
        {text}
        {expanded && overflows && (
          <>
            {" "}
            <button
              type="button"
              onClick={() => setExpanded(false)}
              className="text-primary hover:underline"
            >
              {lessLabel}
            </button>
          </>
        )}
      </p>
      {!expanded && overflows && (
        <button
          type="button"
          onClick={() => setExpanded(true)}
          className="mt-0.5 text-sm text-primary hover:underline"
        >
          {moreLabel}
        </button>
      )}
    </div>
  );
}

/** Rounded card used for the file viewer and the fact sheets. */
export function Panel({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div
      className={cn(
        "overflow-hidden rounded-xl border border-border bg-card/50",
        className,
      )}
    >
      {children}
    </div>
  );
}

/** Label/value rows — "License  MIT", "Version  2.11.2". */
export function FactRows({
  rows,
  className,
}: {
  rows: { label: string; value: ReactNode }[];
  className?: string;
}) {
  const visible = rows.filter((r) => r.value !== null && r.value !== undefined && r.value !== "");
  if (visible.length === 0) return null;
  return (
    <dl className={cn("grid grid-cols-[max-content_minmax(0,1fr)] gap-x-8 gap-y-2.5 text-[15px]", className)}>
      {visible.map((r) => (
        <div key={r.label} className="contents">
          <dt className="text-sm leading-6 text-muted-foreground">{r.label}</dt>
          <dd className="min-w-0 break-words leading-6">{r.value}</dd>
        </div>
      ))}
    </dl>
  );
}

/** Tiny coloured status dot + label. */
export function StatusDot({
  tone,
  label,
  pulse,
}: {
  tone: "ok" | "off" | "warn" | "error" | "busy";
  label: ReactNode;
  pulse?: boolean;
}) {
  const color = {
    ok: "bg-emerald-500",
    off: "bg-muted-foreground/40",
    warn: "bg-amber-500",
    error: "bg-destructive",
    busy: "bg-primary",
  }[tone];
  return (
    <span className="inline-flex items-center gap-2 text-sm text-muted-foreground">
      <span className={cn("h-2 w-2 shrink-0 rounded-full", color, pulse && "animate-pulse")} />
      <span className="truncate">{label}</span>
    </span>
  );
}

/** Short relative/absolute date for the "Last updated" column. */
export function formatShortDate(iso: string | null | undefined, locale?: string): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  try {
    return new Intl.DateTimeFormat(locale, {
      year: "2-digit",
      month: "numeric",
      day: "numeric",
    }).format(d);
  } catch {
    return d.toISOString().slice(0, 10);
  }
}

/** Inline search field that expands from the header's search icon. */
export function InlineSearch({
  value,
  onChange,
  placeholder,
  autoFocus,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder: string;
  autoFocus?: boolean;
}) {
  return (
    <input
      type="text"
      value={value}
      autoFocus={autoFocus}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      aria-label={placeholder}
      className="h-8 w-full rounded-md border border-border bg-background/60 px-3 text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-primary"
    />
  );
}

/** Segmented text filter — "All · Installed · Needs attention". */
export function SegmentedFilter<T extends string>({
  value,
  onChange,
  options,
  label,
}: {
  value: T;
  onChange: (v: T) => void;
  options: { id: T; label: string; count?: number }[];
  label: string;
}) {
  return (
    <div role="tablist" aria-label={label} className="flex items-center gap-1">
      {options.map((o) => {
        const active = o.id === value;
        return (
          <button
            key={o.id}
            type="button"
            role="tab"
            aria-selected={active}
            onClick={() => onChange(o.id)}
            className={cn(
              "inline-flex h-7 items-center gap-1.5 rounded-md px-2.5 text-xs transition-colors",
              active
                ? "bg-sheen/[0.08] font-medium text-foreground"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            {o.label}
            {typeof o.count === "number" && (
              <span className={cn("tabular-nums", active ? "text-muted-foreground" : "text-muted-foreground/60")}>
                {o.count}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}
