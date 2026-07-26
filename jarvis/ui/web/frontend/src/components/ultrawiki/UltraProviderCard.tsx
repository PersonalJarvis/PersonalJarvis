/**
 * One provider card inside an UltraWiki capability slot.
 *
 * Deliberately the same card as the API-Keys view's `ProviderCard`, down to
 * the details that make it read as one product: `card-outline`, the primary
 * ring on the active card, the state chip, a radio "Set active" control on the
 * right (NOT a second button repeating what the chip already says), the shared
 * `ApiKeyForm` credential widget, and a footer separated from the body.
 *
 * Sharing `ApiKeyForm` rather than copying it is the point: the entry, the
 * reveal toggle, the shared-key delete warning and the "get your key" link
 * behave identically here and in the API-Keys view, forever.
 *
 * Cards sit in a grid, so each one stretches to the row height and pins its
 * credential block to the bottom — otherwise a provider with three lines of
 * help text and one with six produce the ragged, unfinished look that reads as
 * "generated" rather than designed.
 */
import type { ReactNode } from "react";
import { Sparkles } from "lucide-react";

import { ApiKeyForm } from "@/components/ApiKeyForm";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";
import type { UltraWikiCatalogRow } from "@/lib/ultrawikiApi";
import {
  isSubscriptionAuthMode,
  UltraSubscriptionAuth,
} from "@/components/ultrawiki/UltraSubscriptionAuth";

const STATE_CHIP_TONE = {
  active: "border-primary/40 bg-primary/15 text-primary font-semibold",
  ready: "border-emerald-500/30 bg-emerald-500/10 text-emerald-600",
  missing: "border-destructive/30 bg-destructive/10 text-destructive",
  neutral: "border-border bg-muted text-muted-foreground",
} as const;

export function StateChip({
  tone,
  children,
}: {
  tone: keyof typeof STATE_CHIP_TONE;
  children: ReactNode;
}): JSX.Element {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-medium",
        STATE_CHIP_TONE[tone],
      )}
    >
      {children}
    </span>
  );
}

/**
 * The radio "Set active" control, identical in shape and wording to the one on
 * the API-Keys cards. A radio, not a button: it says "one of these is chosen"
 * at a glance, which a row of identical buttons never does.
 */
export function SlotActiveControl({
  slot,
  selected,
  busy,
  onSelect,
  title,
  testId,
}: {
  slot: string;
  selected: boolean;
  busy: boolean;
  onSelect: () => void;
  title?: string;
  testId?: string;
}): JSX.Element {
  const t = useT();
  return (
    <label
      onClick={(e) => e.stopPropagation()}
      className={cn(
        "inline-flex shrink-0 cursor-pointer select-none items-center gap-1.5 text-xs",
        selected ? "font-medium text-primary" : "text-muted-foreground hover:text-foreground",
      )}
      title={title}
    >
      <input
        type="radio"
        name={`ultrawiki-slot-${slot}`}
        checked={selected}
        onChange={() => onSelect()}
        disabled={busy}
        data-testid={testId}
        className="accent-primary"
      />
      {t(selected ? "ultrawiki.card.in_use" : "ultrawiki.card.use")}
    </label>
  );
}

export function UltraProviderCard({
  row,
  busy,
  onSelect,
  onCredentialChanged,
  children,
  footer,
}: {
  row: UltraWikiCatalogRow;
  busy: boolean;
  /** Switch this slot to this provider. */
  onSelect: () => void;
  /** A credential was saved or deleted — refetch the catalog. */
  onCredentialChanged: () => void;
  /** Slot-specific body (model picker, connect wizard, server URL). */
  children?: ReactNode;
  /** Slot-specific footer under the separator. */
  footer?: ReactNode;
}): JSX.Element {
  const t = useT();
  const subscriptionAuthMode = isSubscriptionAuthMode(row.auth_mode)
    ? row.auth_mode
    : null;
  const keyless = row.auth_mode === "none" || subscriptionAuthMode !== null;
  const managed = row.auth_mode === "managed_link";

  // A card click selects, EXCEPT on an interactive child — clicking into the
  // password box or the trash icon must never also flip the provider. Same
  // guard as the API-Keys card, and for the same reason it was added there.
  function handleCardActivate(e: React.MouseEvent<HTMLDivElement>) {
    const target = e.target as HTMLElement | null;
    if (
      target &&
      (target.closest("input") ||
        target.closest("button") ||
        target.closest("a") ||
        target.closest("select") ||
        target.closest("label"))
    ) {
      return;
    }
    if (!row.selected) onSelect();
  }

  return (
    <div
      onClick={handleCardActivate}
      title={
        row.selected
          ? t("ultrawiki.card.in_use_tooltip")
          : t("ultrawiki.card.click_to_use")
      }
      data-testid={`ultrawiki-card-${row.slot}-${row.id}`}
      data-selected={row.selected ? "true" : "false"}
      className={cn(
        "card-outline flex h-full flex-col gap-3 p-4 transition-colors",
        row.selected
          ? "border-primary bg-primary/[0.06] ring-1 ring-primary/30"
          : "cursor-pointer hover:border-primary/40 hover:bg-primary/[0.02]",
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-display text-sm font-semibold tracking-tight">
              {row.label}
            </span>
            {/* The chip carries only what the radio does NOT already say. */}
            {!row.selected && row.ready && (
              <StateChip tone="ready">{t("ultrawiki.card.chip_ready")}</StateChip>
            )}
            {!row.selected && !row.ready && (
              <StateChip tone="neutral">
                {t("ultrawiki.card.chip_needs_setup")}
              </StateChip>
            )}
            {row.recommended && (
              <span className="inline-flex items-center gap-1 rounded-full border border-primary/40 bg-primary/10 px-2 py-0.5 text-[10px] font-medium text-primary">
                <Sparkles aria-hidden="true" className="h-2.5 w-2.5" />
                {t("ultrawiki.card.recommended")}
              </span>
            )}
            {row.caution && (
              <span
                className="rounded-full border border-amber-500/40 bg-amber-500/10 px-2 py-0.5 text-[10px] font-medium text-amber-600 dark:text-amber-400"
                title={row.caution}
              >
                {t("ultrawiki.card.not_recommended")}
              </span>
            )}
          </div>
          <p className="mt-0.5 text-[11px] text-muted-foreground">
            <code className="font-mono">{row.id}</code>
            {" · "}
            <span>{t(`ultrawiki.card.auth_${row.auth_mode}`)}</span>
          </p>
        </div>
        <SlotActiveControl
          slot={row.slot}
          selected={row.selected}
          busy={busy}
          onSelect={onSelect}
          testId={`ultrawiki-use-${row.slot}-${row.id}`}
          title={
            row.selected
              ? t("ultrawiki.card.in_use_tooltip")
              : t("ultrawiki.card.click_to_use")
          }
        />
      </div>

      <p className="text-[11px] leading-relaxed text-muted-foreground">
        {row.credential_help}
      </p>

      {/* The honest reason line. Amber, not red: a provider without a key is
          not broken, it is simply not set up — and for every slot but the
          embedding one, Jarvis keeps working without it. */}
      {!row.ready && row.reason && (
        <p
          className="text-[11px] leading-relaxed text-[#ffb84d]"
          data-testid={`ultrawiki-reason-${row.slot}-${row.id}`}
        >
          {row.reason}
        </p>
      )}

      {subscriptionAuthMode && (
        <UltraSubscriptionAuth
          authMode={subscriptionAuthMode}
          onChanged={onCredentialChanged}
        />
      )}

      {children}

      {/* Credentials sit at the bottom of every card, so a row of cards lines
          its input boxes up instead of scattering them by help-text length.
          A managed_link provider owns its own multi-step flow through
          `children` and gets no default key boxes — asking for two secrets the
          user should never type by hand. */}
      <div className="mt-auto space-y-2 pt-1">
        {!keyless &&
          !managed &&
          row.secret_keys.map((secretKey) => (
            <ApiKeyForm
              key={secretKey}
              secretKey={secretKey}
              dashboardUrl={row.dashboard_url}
              configured={Boolean(row.secrets_set[secretKey])}
              sharedWith={row.secret_shared_with[secretKey] ?? []}
              onChanged={onCredentialChanged}
            />
          ))}
        {footer && (
          <div className="border-t border-border/60 pt-2.5">{footer}</div>
        )}
      </div>
    </div>
  );
}
