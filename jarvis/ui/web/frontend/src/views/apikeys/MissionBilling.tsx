import { useCallback, useEffect, useId, useRef, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { CreditCard, Loader2 } from "lucide-react";
import { MISSION_BILLING_KEY, fetchMissionBilling, saveMissionBilling } from "@/components/missions/api";
import { ProviderLogo } from "@/components/providers/ProviderLogo";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { fill, useT } from "@/i18n";
import { APIKEYS_TAB_EVENT, MISSION_BILLING_ANCHOR, takeApiKeysAnchor } from "@/lib/apiKeysTab";
import { SettingsRow, SettingsSection } from "./settingsUi";

/** How a provider slug reads on this page: its company name and mark. */
export type ProviderNamer = (provider: string) => { label: string; logoId: string } | null;

/** "$2" for a whole amount, "$0.42" otherwise. */
function usd(value: number): string {
  return Number.isInteger(value) ? `$${value}` : `$${value.toFixed(2)}`;
}

/**
 * Whether missions continue on the user's own API key once every connected
 * subscription is used up. Off by default: a mission then waits for a
 * subscription, or for a one-off approval in Artifacts.
 *
 * Turning it on bills the user, so it asks first and explains the costs in
 * the dialog; nothing is saved before the confirmation, and the switch shows
 * on only once the server holds it. Turning it off saves at once — the safe
 * direction — and springs back with the error if the save fails.
 */
export function MissionBilling({ providerName }: { providerName: ProviderNamer }) {
  const t = useT();
  const client = useQueryClient();
  const statusId = useId();
  const switchRef = useRef<HTMLButtonElement>(null);
  const query = useQuery({ queryKey: MISSION_BILLING_KEY, queryFn: fetchMissionBilling, staleTime: 30_000 });
  const billing = query.data ?? null;
  const [pending, setPending] = useState<"on" | "off" | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dialogError, setDialogError] = useState<string | null>(null);

  // A "set this up" link from a waiting mission lands here.
  const loaded = billing !== null;
  const reveal = useCallback(() => {
    if (!takeApiKeysAnchor(MISSION_BILLING_ANCHOR)) return;
    document.getElementById(MISSION_BILLING_ANCHOR)?.scrollIntoView?.({ block: "center", behavior: "smooth" });
    switchRef.current?.focus({ preventScroll: true });
  }, []);
  useEffect(() => {
    if (!loaded) return;
    reveal();
    window.addEventListener(APIKEYS_TAB_EVENT, reveal);
    return () => window.removeEventListener(APIKEYS_TAB_EVENT, reveal);
  }, [loaded, reveal]);

  // An older backend has no such setting: leave the section out.
  if (query.isSuccess && billing === null) return null;

  const saveError = (cause: unknown) =>
    fill(t("mission_billing.save_error"), { detail: (cause as Error).message || String(cause) });

  async function turnOff() {
    setError(null);
    setPending("off");
    try {
      client.setQueryData(MISSION_BILLING_KEY, await saveMissionBilling(false));
    } catch (cause) {
      setError(saveError(cause));
    } finally {
      setPending(null);
    }
  }

  async function confirmOn() {
    setDialogError(null);
    setPending("on");
    try {
      client.setQueryData(MISSION_BILLING_KEY, await saveMissionBilling(true));
      setConfirming(false);
    } catch (cause) {
      setDialogError(saveError(cause));
    } finally {
      setPending(null);
    }
  }

  const priced = billing?.paid_provider?.price_known ? billing.paid_provider : null;
  const named = priced ? providerName(priced.provider) : null;
  const providerLabel = named?.label ?? priced?.provider ?? "";
  const on = billing !== null && billing.paid_api_fallback && pending !== "off";
  // Off can always be chosen; on only where it would do something.
  const canTurnOn = billing !== null && billing.subscription_mode && priced !== null;
  const caps = billing
    ? { mission_cap: usd(billing.per_mission_cap_usd), daily_cap: usd(billing.daily_cap_usd) }
    : { mission_cap: "", daily_cap: "" };

  let status: string;
  if (query.isError) {
    status = fill(t("mission_billing.load_error"), { detail: (query.error as Error).message });
  } else if (!billing) {
    status = t("providers_page.checking");
  } else {
    const parts = [
      !billing.subscription_mode
        ? t("mission_billing.state_key_only")
        : on
          ? priced
            ? fill(t("mission_billing.state_on"), { provider: providerLabel, ...caps })
            : t("mission_billing.state_on_no_key")
          : priced
            ? t("mission_billing.state_off")
            : t("mission_billing.state_no_key"),
    ];
    const spent = billing.spent_last_24h_usd;
    if (spent > 0) {
      parts.push(
        fill(t("mission_billing.spent"), {
          amount: spent < 0.01 ? t("mission_billing.less_than_cent") : usd(Number(spent.toFixed(2))),
        }),
      );
    }
    status = parts.join(" · ");
  }

  const title = t("mission_billing.title");

  return (
    <SettingsSection
      id={MISSION_BILLING_ANCHOR}
      title={t("mission_billing.section")}
      data-testid="mission-billing"
      className="scroll-mt-6"
    >
      <SettingsRow
        title={title}
        status={
          <span className="block space-y-0.5">
            <span id={statusId} className={query.isError ? "block text-destructive" : "block"}>
              {status}
            </span>
            {error ? (
              <span role="alert" className="block text-destructive">
                {error}
              </span>
            ) : null}
          </span>
        }
        control={
          <>
            {pending === "off" && <Loader2 aria-hidden="true" className="h-4 w-4 animate-spin text-muted-foreground" />}
            <Switch
              ref={switchRef}
              data-testid="mission-billing-switch"
              checked={on}
              disabled={billing === null || pending !== null || (!on && !canTurnOn)}
              onCheckedChange={(next) => {
                if (next) {
                  setError(null);
                  setDialogError(null);
                  setConfirming(true);
                } else {
                  void turnOff();
                }
              }}
              aria-label={title}
              aria-describedby={statusId}
            />
          </>
        }
      />

      <Dialog.Root
        open={confirming}
        onOpenChange={(open) => {
          if (!open && pending === null) setConfirming(false);
        }}
      >
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 z-[80] bg-scrim/60 backdrop-blur-sm data-[state=open]:animate-in data-[state=open]:fade-in-0" />
          <Dialog.Content
            data-testid="mission-billing-confirm"
            onOpenAutoFocus={(event) => {
              // The safe choice takes the focus: Enter never bills by accident.
              event.preventDefault();
              (event.currentTarget as HTMLElement).querySelector<HTMLElement>("[data-billing-cancel]")?.focus();
            }}
            className="fixed left-1/2 top-1/2 z-[90] max-h-[calc(100dvh-2rem)] w-[min(440px,calc(100vw-2rem))] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-2xl border border-border bg-popover p-6 text-popover-foreground shadow-2xl data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95"
          >
            <div className="flex items-start gap-4">
              <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-warning/10 text-warning">
                <CreditCard className="h-[18px] w-[18px]" aria-hidden />
              </div>
              <div className="min-w-0 flex-1">
                <Dialog.Title className="text-base font-semibold">{t("mission_billing.confirm_title")}</Dialog.Title>
                <Dialog.Description className="mt-1.5 text-sm leading-relaxed text-muted-foreground">
                  {t("mission_billing.confirm_intro")}
                </Dialog.Description>
              </div>
            </div>

            <div className="mt-4 space-y-3 text-sm leading-relaxed text-muted-foreground">
              {priced ? (
                <div className="space-y-2">
                  <p>{fill(t("mission_billing.confirm_billing"), { provider: providerLabel })}</p>
                  <div
                    data-testid="mission-billing-provider"
                    className="flex min-w-0 items-center gap-2.5 rounded-xl border border-border bg-muted/40 px-3 py-2.5"
                  >
                    <ProviderLogo
                      providerId={named?.logoId ?? priced.provider}
                      label={providerLabel}
                      size="sm"
                      className="h-5 w-5 shrink-0"
                    />
                    <span className="truncate font-medium text-foreground">{providerLabel}</span>
                    <code className="ml-auto min-w-0 truncate font-mono text-xs text-muted-foreground">{priced.model}</code>
                  </div>
                </div>
              ) : (
                <p className="text-warning">{t("mission_billing.confirm_no_key")}</p>
              )}
              <p>{fill(t("mission_billing.confirm_caps"), caps)}</p>
              <p>{t("mission_billing.confirm_off")}</p>
              {dialogError ? (
                <p role="alert" className="text-destructive">
                  {dialogError}
                </p>
              ) : null}
            </div>

            <div className="mt-6 flex flex-wrap justify-end gap-2">
              <Button
                type="button"
                size="sm"
                variant="outline"
                data-billing-cancel
                disabled={pending !== null}
                onClick={() => setConfirming(false)}
              >
                {t("common.cancel")}
              </Button>
              <Button
                type="button"
                size="sm"
                data-testid="mission-billing-confirm-on"
                disabled={pending !== null || !canTurnOn}
                onClick={() => void confirmOn()}
              >
                {pending === "on" && <Loader2 className="animate-spin" aria-hidden />}
                {t("mission_billing.confirm_action")}
              </Button>
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </SettingsSection>
  );
}
