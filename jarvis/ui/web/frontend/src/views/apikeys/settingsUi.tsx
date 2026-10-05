import type { ComponentPropsWithoutRef, ReactNode } from "react";
import { cn } from "@/lib/utils";

/**
 * The compact settings grammar of the provider page.
 *
 * A section is a muted one-line title (with an optional action on the right)
 * above one card. A row inside the card is a title and a short explanation on
 * the left and its control on the right; on a narrow window the control drops
 * under the text. Explanations belong to rows, never to section titles.
 */
export function SettingsSection({
  title,
  icon,
  headerAction,
  plain = false,
  children,
  className,
  ...props
}: Omit<ComponentPropsWithoutRef<"section">, "title"> & {
  title: ReactNode;
  icon?: ReactNode;
  headerAction?: ReactNode;
  /** No card around the children (they bring their own surfaces). */
  plain?: boolean;
  children: ReactNode;
}) {
  return (
    <section {...props} className={cn("space-y-2.5", className)}>
      <div className="flex min-h-7 items-center justify-between gap-4 px-1">
        <h2 className="flex min-h-7 min-w-0 items-center gap-2 text-sm font-normal text-foreground/70">
          {icon}
          <span className="truncate">{title}</span>
        </h2>
        {headerAction ? <div className="flex min-w-7 shrink-0 items-center justify-end gap-1">{headerAction}</div> : null}
      </div>
      {plain ? children : <SettingsGroup>{children}</SettingsGroup>}
    </section>
  );
}

/** The card surface: rounded, hairline border, rows divided by hairlines. */
export function SettingsGroup({
  divided = true,
  className,
  ...props
}: ComponentPropsWithoutRef<"div"> & { divided?: boolean }) {
  return (
    <div
      {...props}
      className={cn(
        "relative overflow-hidden rounded-xl border border-border/60 bg-card/40 text-foreground",
        divided && "[&>*+*]:border-t [&>*+*]:border-border/50",
        className,
      )}
    />
  );
}

export function SettingsRow({
  title,
  description,
  status,
  control,
  children,
  className,
  ...props
}: Omit<ComponentPropsWithoutRef<"div">, "title"> & {
  title: ReactNode;
  description?: ReactNode;
  /** A live state line under the description ("Authenticated as …"). */
  status?: ReactNode;
  control?: ReactNode;
  /** Full-width content under the row (an input, a list). */
  children?: ReactNode;
}) {
  return (
    <div {...props} data-slot="settings-row" className={cn("px-4 py-3", className)}>
      <div className="flex flex-col gap-3 sm:grid sm:grid-cols-[minmax(0,1fr)_minmax(10rem,auto)] sm:items-center sm:gap-8">
        <div className="min-w-0 space-y-1">
          <h3 className="flex min-h-5 items-center gap-1.5 text-sm font-medium text-foreground">{title}</h3>
          {description ? (
            <p className="max-w-xl text-xs leading-normal text-muted-foreground/80">{description}</p>
          ) : null}
          {status ? <div className="pt-0.5 text-xs text-muted-foreground">{status}</div> : null}
        </div>
        {control ? (
          <div className="flex w-full min-w-0 shrink-0 items-center gap-2 sm:w-auto sm:justify-end">
            {control}
          </div>
        ) : null}
      </div>
      {children ? <div className="pt-3">{children}</div> : null}
    </div>
  );
}

/**
 * A list beside the selected item's settings, inside one card that fills the
 * window below the page header, so the section is one view: the list sits on
 * a slightly darker ground and both halves scroll on their own.
 */
export function MasterDetail({
  list,
  detail,
  testId,
}: {
  list: ReactNode;
  detail: ReactNode;
  testId?: string;
}) {
  return (
    <SettingsGroup
      divided={false}
      data-testid={testId}
      className="md:grid md:h-[calc(100dvh-17.5rem)] md:min-h-[24rem] md:grid-cols-[17rem_minmax(0,1fr)]"
    >
      <div className="border-b border-border/60 bg-secondary/30 md:min-h-0 md:overflow-y-auto md:border-b-0 md:border-r scrollbar-jarvis">
        <div className="divide-y divide-border/50">{list}</div>
      </div>
      <div className="min-w-0 md:min-h-0 md:overflow-y-auto scrollbar-jarvis">
        <div className="space-y-6 p-4">{detail}</div>
      </div>
    </SettingsGroup>
  );
}

/** One entry of a `MasterDetail` list: mark, name, version, status, and a switch. */
export function ListRow({
  icon,
  name,
  version,
  status,
  dot,
  selected,
  dimmed,
  onSelect,
  trailing,
  testId,
}: {
  icon: ReactNode;
  name: string;
  version?: string | null;
  status: ReactNode;
  /** Only trouble gets a dot; a healthy row reads fine from its text. */
  dot?: "warning" | "error" | null;
  selected: boolean;
  dimmed: boolean;
  onSelect: () => void;
  trailing?: ReactNode;
  testId?: string;
}) {
  return (
    <div
      data-testid={testId}
      className={cn(
        "group flex min-h-[4.5rem] items-center gap-3 px-4 py-3 transition-colors",
        selected ? "bg-secondary" : "hover:bg-secondary/50",
      )}
    >
      <div
        className={cn(
          "relative flex min-w-0 flex-1 items-start gap-3 rounded-md text-left transition-opacity",
          dimmed && !selected && "opacity-60 group-hover:opacity-100",
        )}
      >
        <button
          type="button"
          className="absolute inset-0 cursor-pointer rounded-md outline-none focus-visible:ring-2 focus-visible:ring-ring"
          onClick={onSelect}
          aria-label={name}
          aria-pressed={selected}
        />
        <span className="pointer-events-none mt-0.5 flex shrink-0">{icon}</span>
        <span className="pointer-events-none min-w-0 flex-1">
          <span className="flex min-w-0 items-center gap-2">
            <span className="truncate text-sm font-medium text-foreground">{name}</span>
            {version ? <code className="max-w-24 shrink-0 truncate text-xs text-muted-foreground">{version}</code> : null}
          </span>
          <span className="mt-0.5 flex items-start gap-1.5 text-xs leading-normal text-muted-foreground/80">
            {dot ? (
              <span className="flex h-[1.45em] shrink-0 items-center">
                <span
                  aria-hidden="true"
                  className={cn("h-1.5 w-1.5 rounded-full", dot === "error" ? "bg-destructive" : "bg-warning")}
                />
              </span>
            ) : null}
            <span className="line-clamp-2 [overflow-wrap:anywhere]">{status}</span>
          </span>
        </span>
      </div>
      {trailing ? <span className="flex h-5 shrink-0 items-center">{trailing}</span> : null}
    </div>
  );
}

/**
 * A small segmented choice ("Subscription | API key"). Each option is a radio:
 * exactly one is picked, and arrow keys are not needed for two or three.
 */
export function Segmented<T extends string>({
  value,
  options,
  onChange,
  label,
  disabled,
  testId,
  size = "sm",
}: {
  value: T;
  options: { value: T; label: string; disabled?: boolean }[];
  /** `xs` is the small switch that sits inside a row of a card. */
  size?: "sm" | "xs";
  onChange: (value: T) => void;
  label: string;
  disabled?: boolean;
  testId?: string;
}) {
  return (
    <div
      role="radiogroup"
      aria-label={label}
      data-testid={testId}
      className="inline-flex shrink-0 rounded-lg border border-border/60 bg-card/40 p-0.5"
    >
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          role="radio"
          aria-checked={value === option.value}
          disabled={disabled || option.disabled}
          onClick={() => value !== option.value && onChange(option.value)}
          className={cn(
            size === "xs" ? "h-6 rounded-md px-2 text-xs font-medium transition-colors" : "h-7 rounded-md px-3 text-sm font-medium transition-colors",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50",
            value === option.value ? "bg-secondary text-foreground" : "text-muted-foreground hover:text-foreground",
          )}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

/**
 * A list of models inside a section card: name, id, a short note, and one
 * control per row (a visibility switch, or the pick mark of a single choice).
 * The header line carries a count and an optional bulk action.
 */
export function ModelList({
  header,
  action,
  children,
  testId,
}: {
  header: ReactNode;
  action?: ReactNode;
  children: ReactNode;
  testId?: string;
}) {
  return (
    <SettingsGroup divided={false} data-testid={testId}>
      <div className="flex min-h-10 items-center justify-between gap-4 border-b border-border/50 px-4 py-2 text-xs text-muted-foreground">
        <span>{header}</span>
        {action}
      </div>
      <ul className="max-h-[22rem] overflow-y-auto py-1 scrollbar-jarvis">{children}</ul>
    </SettingsGroup>
  );
}

export function ModelListRow({
  name,
  id,
  note,
  control,
  muted,
  onClick,
  selected,
  disabled,
}: {
  name: string;
  id?: string;
  note?: string | null;
  control: ReactNode;
  muted?: boolean;
  /** A single-choice list makes the whole row the hit target. */
  onClick?: () => void;
  selected?: boolean;
  disabled?: boolean;
}) {
  const body = (
    <>
      <span className={cn("flex min-w-0 flex-1 items-baseline gap-2", muted && "opacity-60")}>
        <span className="truncate text-sm text-foreground">{name}</span>
        {id && id !== name ? <code className="truncate text-xs text-muted-foreground">{id}</code> : null}
      </span>
      {note ? <span className="hidden shrink-0 text-xs text-muted-foreground sm:inline">{note}</span> : null}
      <span className="flex h-5 shrink-0 items-center">{control}</span>
    </>
  );
  return (
    <li>
      {onClick ? (
        <button
          type="button"
          role="radio"
          aria-checked={Boolean(selected)}
          aria-label={name}
          disabled={disabled}
          onClick={onClick}
          className={cn(
            "flex w-full items-center gap-3 px-4 py-2 text-left transition-colors",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring disabled:cursor-not-allowed",
            selected ? "bg-secondary/70" : "hover:bg-secondary/40",
          )}
        >
          {body}
        </button>
      ) : (
        <div className="flex items-center gap-3 px-4 py-2">{body}</div>
      )}
    </li>
  );
}

/** The detail pane's title line: mark and name, and the version on the right. */
export function DetailHeader({ icon, name, version }: { icon: ReactNode; name: string; version?: string | null }) {
  return (
    <div className="flex min-h-7 items-center justify-between gap-4 px-1">
      <h2 className="flex min-w-0 items-center gap-2 text-sm font-normal text-foreground/70">
        {icon}
        <span className="truncate">{name}</span>
      </h2>
      {version ? <code className="truncate text-xs text-muted-foreground">{version}</code> : null}
    </div>
  );
}
