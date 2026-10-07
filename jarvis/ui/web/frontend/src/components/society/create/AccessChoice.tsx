/**
 * "How it pays" in the New-agent dialog: subscription, API key or local, as
 * one radio group. Arrow keys move the choice (one tab stop, the WAI-ARIA
 * radio pattern); an access the provider refuses right now (`blocked`) is
 * shown disabled with its reason, so the person learns why before the first
 * message would fail.
 */
import { useId, useRef, type KeyboardEvent } from "react";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { blockedReasonKey, type AccessOption } from "./seatChoice";
import { XaiConnect } from "./XaiConnect";

export function AccessChoice({ options, value, hint, disabled, onChange }: {
  options: AccessOption[];
  /** The picked kind; "" when no access can be picked. */
  value: string;
  /** The sentence under the group that explains the picked access. */
  hint: string;
  disabled: boolean;
  onChange: (kind: string) => void;
}) {
  const t = useT();
  const id = useId();
  const buttons = useRef<(HTMLButtonElement | null)[]>([]);
  const open = options.filter((option) => !option.blocked);
  const focusKind = open.some((option) => option.kind === value) ? value : open[0]?.kind ?? "";

  function onKeyDown(event: KeyboardEvent<HTMLButtonElement>, kind: string) {
    const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[event.key];
    if (!step || open.length < 2) return;
    event.preventDefault();
    const index = open.findIndex((option) => option.kind === kind);
    const next = open[(index + step + open.length) % open.length];
    onChange(next.kind);
    buttons.current[options.indexOf(next)]?.focus();
  }

  return (
    <div className="flex flex-col gap-1.5 text-sm">
      <span className="font-medium" id={`${id}-label`}>{t("society.create_agent.access_label")}</span>
      <div
        className="flex gap-1 rounded-lg border border-border p-1"
        role="radiogroup"
        aria-labelledby={`${id}-label`}
        aria-describedby={hint ? `${id}-hint` : undefined}
        data-testid="create-agent-access"
      >
        {options.map((entry, index) => {
          const checked = entry.kind === value && !entry.blocked;
          return (
            <button
              key={entry.kind}
              ref={(node) => { buttons.current[index] = node; }}
              type="button"
              role="radio"
              aria-checked={checked}
              aria-describedby={entry.blocked ? `${id}-blocked-${entry.kind}` : undefined}
              tabIndex={entry.kind === focusKind && !entry.blocked ? 0 : -1}
              disabled={disabled || Boolean(entry.blocked)}
              onClick={() => onChange(entry.kind)}
              onKeyDown={(event) => onKeyDown(event, entry.kind)}
              className={cn(
                "min-w-0 flex-1 truncate rounded-md px-3 py-1.5 text-sm transition-colors disabled:opacity-50",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                checked
                  ? "bg-secondary text-foreground"
                  : "text-muted-foreground hover:bg-secondary hover:text-foreground",
                entry.blocked && "line-through hover:bg-transparent hover:text-muted-foreground",
              )}
            >
              {t(`society.create.kind_${entry.kind}`)}
            </button>
          );
        })}
      </div>
      {hint ? <p id={`${id}-hint`} className="text-xs text-muted-foreground">{hint}</p> : null}
      {options.filter((entry) => entry.blocked).map((entry) => (
        <div key={entry.kind} className="flex flex-col gap-1.5">
          <p id={`${id}-blocked-${entry.kind}`} role="note" className="text-xs text-warning"
            data-testid={`create-agent-access-blocked-${entry.kind}`}>
            {t(blockedReasonKey(entry.blocked ?? ""))}
          </p>
          {/* The one refusal the person can lift right here: connect Grok for agents. */}
          {entry.blocked === "xai_login_needed" ? <XaiConnect disabled={disabled} /> : null}
        </div>
      ))}
    </div>
  );
}
