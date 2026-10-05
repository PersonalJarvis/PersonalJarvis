import { useState } from "react";
import { AlertTriangle, ExternalLink, Eye, EyeOff, Loader2, Trash2 } from "lucide-react";
import { ProviderTestControl } from "@/components/providers/ProviderTierSection";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { deleteSecret, postSecret, type ProviderDescriptor } from "@/hooks/useProviders";
import { useT } from "@/i18n";
import { keyMatchesSecret } from "@/lib/keyFormat";
import { openExternalUrl } from "@/lib/openExternal";
import type { ProviderFamily } from "@/lib/providerFamilies";
import { useEventStore } from "@/store/events";
import { ProfileGroup, SettingRow } from "@/views/profile/ProfileGroup";
import type { FamilyState } from "./familyState";

/** Which member card a key test runs against: the first keyed card, chat first. */
const TEST_TIER_ORDER: ProviderDescriptor["tier"][] = ["brain", "realtime", "tts", "stt", "dictation"];

function announce(slot: string, action: "set" | "delete") {
  // Same signal the per-card forms send: every mounted list and the health
  // dots re-read. Only the slot NAME travels, never a value.
  window.dispatchEvent(new CustomEvent("jarvis:secret-configured", { detail: { key: slot, action } }));
}

/**
 * One key for the whole company. Saving always uses the "everywhere" scope:
 * the key becomes the family key, so live voice, agents, speech and every
 * other feature of this company read it — the "one provider, one key" rule.
 * A feature that still holds an older key of its own is listed underneath
 * with a one-click way back onto the main key.
 */
export function KeySection({
  family,
  state,
  onChanged,
}: {
  family: ProviderFamily;
  state: FamilyState;
  onChanged: () => void | Promise<void>;
}) {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);
  const slot = family.key_slot!;
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState("");
  const [reveal, setReveal] = useState(false);
  const [busy, setBusy] = useState<"save" | "delete" | string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const showInput = !family.key_present || editing;
  const format = value.trim() ? keyMatchesSecret(slot, value) : null;
  const testCard = TEST_TIER_ORDER.map((tier) =>
    state.members.find((m) => m.tier === tier && m.auth_mode === "api_key"),
  ).find(Boolean);

  async function save() {
    const trimmed = value.trim();
    if (!trimmed) return;
    setBusy("save");
    try {
      await postSecret(slot, trimmed, "everywhere");
      setValue("");
      setEditing(false);
      setReveal(false);
      announce(slot, "set");
      pushToast("success", t("providers_page.key_saved_toast").replace("{0}", family.label));
      await onChanged();
    } catch (cause) {
      pushToast("error", (cause as Error).message);
    } finally {
      setBusy(null);
    }
  }

  async function remove(target: string, toast: string) {
    setBusy(target === slot ? "delete" : target);
    try {
      await deleteSecret(target);
      announce(target, "delete");
      pushToast("info", toast);
      await onChanged();
    } catch (cause) {
      pushToast("error", (cause as Error).message);
    } finally {
      setBusy(null);
      setConfirmDelete(false);
    }
  }

  return (
    <ProfileGroup
      title={t("providers_page.key_title")}
      description={t("providers_page.key_desc").replace("{0}", family.label)}
      testId="provider-key"
      aside={
        family.dashboard_url ? (
          <Button
            size="sm"
            variant="ghost"
            className="text-muted-foreground"
            onClick={() => void openExternalUrl(family.dashboard_url!)}
          >
            <ExternalLink />
            {t("providers_page.get_key")}
          </Button>
        ) : undefined
      }
    >
      <SettingRow
        testId="provider-key-row"
        label={
          <span className="inline-flex items-center gap-2">
            <span
              aria-hidden="true"
              className={family.key_present ? "h-2 w-2 rounded-full bg-success" : "h-2 w-2 rounded-full bg-border-strong"}
            />
            {t("providers_page.key_label")}
          </span>
        }
        hint={
          family.key_present
            ? t("providers_page.key_saved_hint")
            : state.viaProject
              ? t("providers_page.key_project_hint")
              : t("providers_page.key_missing_hint")
        }
        control={
          family.key_present && !editing ? (
            confirmDelete ? (
              <>
                <span className="text-sm text-muted-foreground">{t("providers_page.key_remove_confirm")}</span>
                <Button size="sm" variant="ghost" onClick={() => setConfirmDelete(false)}>
                  {t("common.cancel")}
                </Button>
                <Button
                  size="sm"
                  variant="destructive"
                  data-testid="provider-key-remove-confirm"
                  disabled={busy !== null}
                  onClick={() => void remove(slot, t("providers_page.key_removed_toast").replace("{0}", family.label))}
                >
                  {busy === "delete" && <Loader2 className="animate-spin" />}
                  {t("providers_page.key_remove")}
                </Button>
              </>
            ) : (
              <>
                <Button size="sm" variant="outline" data-testid="provider-key-replace" onClick={() => setEditing(true)}>
                  {t("providers_page.key_replace")}
                </Button>
                <Button
                  size="icon"
                  variant="ghost"
                  className="h-8 w-8 text-muted-foreground"
                  aria-label={t("providers_page.key_remove")}
                  title={t("providers_page.key_remove")}
                  data-testid="provider-key-remove"
                  onClick={() => setConfirmDelete(true)}
                >
                  <Trash2 />
                </Button>
              </>
            )
          ) : undefined
        }
      >
        {showInput && (
          <div className="mt-3 flex flex-col gap-2">
            <div className="flex items-center gap-2">
              <div className="relative min-w-0 flex-1">
                <Input
                  type={reveal ? "text" : "password"}
                  autoComplete="off"
                  spellCheck={false}
                  data-testid="provider-key-input"
                  aria-label={t("providers_page.key_label")}
                  placeholder={t("providers_page.key_placeholder").replace("{0}", family.label)}
                  value={value}
                  onChange={(event) => setValue(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") void save();
                    if (event.key === "Escape" && family.key_present) {
                      setEditing(false);
                      setValue("");
                    }
                  }}
                  className="pr-10 font-mono"
                />
                <button
                  type="button"
                  onClick={() => setReveal((on) => !on)}
                  aria-label={t(reveal ? "providers_page.key_hide" : "providers_page.key_show")}
                  className="absolute right-1 top-1/2 grid h-7 w-7 -translate-y-1/2 place-items-center rounded-md text-muted-foreground hover:bg-secondary hover:text-foreground"
                >
                  {reveal ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                </button>
              </div>
              {editing && (
                <Button size="sm" variant="ghost" onClick={() => { setEditing(false); setValue(""); }}>
                  {t("common.cancel")}
                </Button>
              )}
              <Button
                size="sm"
                data-testid="provider-key-save"
                disabled={!value.trim() || busy !== null}
                onClick={() => void save()}
              >
                {busy === "save" && <Loader2 className="animate-spin" />}
                {t("providers_page.key_save")}
              </Button>
            </div>
            {format && !format.match && format.detected && (
              <p role="status" className="flex items-start gap-1.5 text-sm text-warning">
                <AlertTriangle aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0" />
                {t("providers_page.key_format_warning")
                  .replace("{0}", format.detected.label)
                  .replace("{1}", family.label)}
              </p>
            )}
          </div>
        )}
      </SettingRow>

      {family.key_present && testCard && (
        <SettingRow
          label={t("providers_page.key_test_label")}
          hint={t("providers_page.key_test_hint")}
          control={
            <ProviderTestControl
              providerId={testCard.id}
              providerLabel={testCard.label}
              section={testCard.tier}
              active={testCard.active}
            />
          }
        />
      )}

      {family.separate_keys.map((entry) => (
        <SettingRow
          key={entry.slot}
          testId={`provider-separate-key-${entry.slot}`}
          label={t(`providers_page.separate_${entry.surface}`)}
          hint={t("providers_page.separate_hint")}
          control={
            <Button
              size="sm"
              variant="outline"
              disabled={busy !== null || !family.key_present}
              title={family.key_present ? undefined : t("providers_page.separate_needs_main")}
              onClick={() =>
                void remove(entry.slot, t("providers_page.separate_merged_toast").replace("{0}", family.label))
              }
            >
              {busy === entry.slot && <Loader2 className="animate-spin" />}
              {t("providers_page.separate_merge")}
            </Button>
          }
        />
      ))}
    </ProfileGroup>
  );
}
