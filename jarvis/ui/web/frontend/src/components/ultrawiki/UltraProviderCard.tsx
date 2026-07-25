/**
 * One provider card inside an UltraWiki capability slot.
 *
 * Deliberately the same visual language as the API-Keys view's `ProviderCard`
 * (`card-outline`, the primary ring on the active card, the state chip, the
 * `ApiKeyForm` credential widget, a footer separated from the body): the two
 * screens do the same job — pick a provider, give it a credential, verify it —
 * so they must not look like two different products.
 *
 * The credential widget is literally `ApiKeyForm`, not a copy of it. That is
 * the point of this rewrite: the old settings cards had no credential input at
 * all and told the user to go to the API-Keys view, which has no field for
 * these slots either. Sharing the component means the entry, the reveal
 * toggle, the shared-key delete warning and the "get your key" link behave
 * identically here and there, forever.
 */
import type { ReactNode } from "react";
import { Sparkles } from "lucide-react";

import { ApiKeyForm } from "@/components/ApiKeyForm";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";
import type { UltraWikiCatalogRow } from "@/lib/ultrawikiApi";

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
  /** Slot-specific body (model input, connect wizard, server URL). */
  children?: ReactNode;
  /** Slot-specific footer under the separator (a test result, a hint). */
  footer?: ReactNode;
}): JSX.Element {
  const t = useT();
  const keyless = row.auth_mode === "none";

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
        "card-outline space-y-3 p-4 transition-colors",
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
            <CardStatusChip row={row} />
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
        <Button
          size="sm"
          variant={row.selected ? "secondary" : "outline"}
          disabled={row.selected || busy}
          onClick={onSelect}
          data-testid={`ultrawiki-use-${row.slot}-${row.id}`}
        >
          {t(row.selected ? "ultrawiki.card.in_use" : "ultrawiki.card.use")}
        </Button>
      </div>

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

      {/* A managed_link provider owns its own multi-step flow (Supabase: browser
          login → project pick → password), so the card shows the explanation
          and hands the credential UI to `children`. Rendering the default key
          boxes here as well would ask for two secrets the user should never
          type by hand. */}
      {keyless || row.auth_mode === "managed_link" ? (
        <p className="text-[11px] leading-relaxed text-muted-foreground">
          {row.credential_help}
        </p>
      ) : (
        row.secret_keys.map((secretKey) => (
          <ApiKeyForm
            key={secretKey}
            secretKey={secretKey}
            dashboardUrl={row.dashboard_url}
            configured={Boolean(row.secrets_set[secretKey])}
            sharedWith={row.secret_shared_with[secretKey] ?? []}
            credentialHelp={row.credential_help}
            onChanged={onCredentialChanged}
          />
        ))
      )}

      {children}

      {footer && (
        <div className="border-t border-border/60 pt-2.5">{footer}</div>
      )}
    </div>
  );
}

/** In use → active; usable → ready; otherwise "needs setup". */
function CardStatusChip({ row }: { row: UltraWikiCatalogRow }): JSX.Element {
  const t = useT();
  if (row.selected) return <StateChip tone="active">{t("ultrawiki.card.chip_in_use")}</StateChip>;
  if (row.ready) return <StateChip tone="ready">{t("ultrawiki.card.chip_ready")}</StateChip>;
  return <StateChip tone="neutral">{t("ultrawiki.card.chip_needs_setup")}</StateChip>;
}
