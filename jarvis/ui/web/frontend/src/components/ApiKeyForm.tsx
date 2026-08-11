import { useState } from "react";
import { AlertTriangle, CheckCircle2, Eye, EyeOff, ExternalLink, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { deleteSecret, postSecret } from "@/hooks/useProviders";
import { keyMatchesSecret } from "@/lib/keyFormat";
import { useEventStore } from "@/store/events";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";

interface ApiKeyFormProps {
  secretKey: string;
  dashboardUrl: string | null;
  configured: boolean;
  /**
   * Plain-English "which key, and what for" shown above the input. Optional so
   * existing call sites keep working; the provider catalog supplies it.
   */
  credentialHelp?: string | null;
  /**
   * The runtime can already read a value for this slot through the family
   * fallback chain (e.g. the Realtime card covered by the shared OpenAI
   * key). When true and the dedicated slot is empty, the form renders a
   * collapsed "covered by your shared key" state instead of an empty input
   * demanding a second key the runtime does not need.
   */
  effectiveConfigured?: boolean;
  /**
   * Labels of the OTHER provider surfaces that read this same slot at
   * runtime. Non-empty ⇒ deleting asks for confirmation, because the delete
   * silently disables those surfaces too.
   */
  sharedWith?: string[];
  onChanged?: () => void;
  /**
   * Called after a key has been saved successfully.
   * The parent card decides whether that triggers an auto-switch
   * (e.g. when no one else is active in the tier).
   */
  onSavedActivate?: () => void;
}

/**
 * Tell every mounted surface that this credential slot changed.
 *
 * The parent card's `onChanged` only refreshes the card the form sits on. The
 * same key is shown by other screens (the API-Keys view and the voice
 * section's "API Keys" tab render the same provider block), and the section
 * health rollup has to re-check too. `useProviders` and `useSectionHealth`
 * already listen for this event, so one dispatch keeps them all honest without
 * waiting for a backend broadcast to arrive over the WebSocket. Established
 * in-repo pattern (TelephonyView). Only the key NAME travels — never a value.
 */
function announceSecretChange(secretKey: string, action: "set" | "delete") {
  window.dispatchEvent(
    new CustomEvent("jarvis:secret-configured", {
      detail: { key: secretKey, action },
    }),
  );
}

/**
 * Single-key input form: password input + "Save" + delete action for an
 * existing value. Writes directly to POST /api/secrets/{key}; the value
 * never leaves the frontend again after submit (read-only flag in the backend).
 */
export function ApiKeyForm({ secretKey, dashboardUrl, configured, credentialHelp, effectiveConfigured, sharedWith, onChanged, onSavedActivate }: ApiKeyFormProps) {
  const t = useT();
  const [value, setValue] = useState("");
  const [pending, setPending] = useState(false);
  const [reveal, setReveal] = useState(false);
  const coveredByShared = !configured && Boolean(effectiveConfigured);
  const [editing, setEditing] = useState(!configured && !effectiveConfigured);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const pushToast = useEventStore((s) => s.pushToast);

  // Live, client-side format recognition — the entered value never leaves the
  // browser to be classified (the 2026-06-22 AI-Studio-vs-Vertex mix-up). Only
  // hints; never blocks the save.
  const fmt = value.trim() ? keyMatchesSecret(secretKey, value) : null;

  async function handleSave() {
    const trimmed = value.trim();
    if (!trimmed) return;
    setPending(true);
    try {
      await postSecret(secretKey, trimmed);
      pushToast("success", t("apikeys_view.key_saved_toast").replace("{0}", secretKey));
      setValue("");
      setEditing(false);
      onChanged?.();
      onSavedActivate?.();
      announceSecretChange(secretKey, "set");
    } catch (e) {
      pushToast("error", `${t("common.save_failed")}: ${(e as Error).message}`);
    } finally {
      setPending(false);
    }
  }

  async function handleDelete() {
    // A slot that other tiers also read (one OpenAI key backs Brain, STT,
    // TTS and the Tool Model) never dies on a single click: first click
    // shows which surfaces the delete takes down, second click confirms.
    if ((sharedWith?.length ?? 0) > 0 && !confirmingDelete) {
      setConfirmingDelete(true);
      return;
    }
    setConfirmingDelete(false);
    setPending(true);
    try {
      await deleteSecret(secretKey);
      pushToast("info", t("apikeys_view.key_removed_toast").replace("{0}", secretKey));
      setEditing(true);
      onChanged?.();
      announceSecretChange(secretKey, "delete");
    } catch (e) {
      pushToast("error", `${t("common.delete_failed")}: ${(e as Error).message}`);
    } finally {
      setPending(false);
    }
  }

  // The "get your key" link to the provider's official dashboard. Shown in
  // BOTH states \u2014 while entering a key AND once it's saved \u2014 so the official
  // source is always one click away (rotating a key, checking quota, etc.).
  // The link text names the destination host: for a non-technical user the
  // domain IS the answer to "where do I get this key, and is that site
  // legitimate?" \u2014 a bare "here" answers neither.
  let dashboardHost = "";
  if (dashboardUrl) {
    try {
      dashboardHost = new URL(dashboardUrl).hostname.replace(/^www\./, "");
    } catch {
      // A malformed catalog URL falls back to the generic label below.
    }
  }
  const dashboardLink = dashboardUrl ? (
    <a
      href={dashboardUrl}
      target="_blank"
      rel="noreferrer"
      className="inline-flex items-center gap-1 text-[11px] text-muted-foreground hover:text-primary"
    >
      <ExternalLink className="h-3 w-3" />{" "}
      {dashboardHost
        ? t("apikeys_view.get_key_at").replace("{0}", dashboardHost)
        : t("apikeys_view.get_key_here")}
    </a>
  ) : null;

  const sharedDeleteConfirm = confirmingDelete ? (
    <div className="space-y-2 rounded-md border border-destructive/40 bg-destructive/10 p-2">
      <p className="flex items-start gap-1 text-[11px] text-destructive">
        <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />
        <span>
          {t("apikeys_view.shared_delete_prefix")} {(sharedWith ?? []).join(", ")}.{" "}
          {t("apikeys_view.shared_delete_suffix")}
        </span>
      </p>
      <div className="flex gap-2">
        <Button
          size="sm"
          variant="destructive"
          onClick={handleDelete}
          disabled={pending}
        >
          {t("apikeys_view.delete_anyway")}
        </Button>
        <Button size="sm" variant="ghost" onClick={() => setConfirmingDelete(false)}>
          {t("common.cancel")}
        </Button>
      </div>
    </div>
  ) : null;

  if (configured && !editing) {
    // The saved state says so in words, not only in dots: a masked row alone
    // reads as "there is SOMETHING here", while "Key saved" + a green check
    // answers the actual question ("am I done with this field?") at a glance.
    return (
      <div className="space-y-2">
        <div className="flex items-center gap-2">
          <div className="flex min-w-0 flex-1 items-center gap-2 rounded-md border border-border bg-muted/30 px-3 py-1.5">
            <CheckCircle2 aria-hidden="true" className="h-3.5 w-3.5 shrink-0 text-emerald-500" />
            <span className="shrink-0 text-xs font-medium text-foreground">
              {t("apikeys_view.key_saved_label")}
            </span>
            <code className="truncate font-mono text-xs text-muted-foreground">
              {"\u2022".repeat(12)}
            </code>
          </div>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => setEditing(true)}
            title={t("apikeys_view.replace_tooltip")}
          >
            {t("apikeys_view.replace")}
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={handleDelete}
            disabled={pending}
            aria-label={`Delete ${secretKey}`}
            title={t("apikeys_view.delete_key_tooltip")}
            className="text-destructive hover:text-destructive"
          >
            <Trash2 className="h-3.5 w-3.5" />
          </Button>
        </div>
        {sharedDeleteConfirm}
        {dashboardLink}
      </div>
    );
  }

  if (coveredByShared && !editing) {
    // The runtime already serves this surface from a shared family key
    // (e.g. Realtime running off the one OpenAI key). An empty password box
    // here read as "you must paste a second key" \u2014 render the truth instead,
    // with the dedicated key as an explicit optional upgrade.
    return (
      <div className="space-y-2">
        <p className="text-[11px] leading-relaxed text-muted-foreground">
          {t("apikeys_view.shared_key_covered")}
        </p>
        <Button size="sm" variant="ghost" onClick={() => setEditing(true)}>
          {t("apikeys_view.add_dedicated_key")}
        </Button>
        {dashboardLink}
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {credentialHelp && (
        <p className="text-[11px] leading-relaxed text-muted-foreground">{credentialHelp}</p>
      )}
      <div className="flex gap-2">
        <div className="relative flex-1">
          <input
            type={reveal ? "text" : "password"}
            aria-label={`Enter ${secretKey}`}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            // Human words, not the internal slot name: `Enter openai_api_key…`
            // is developer vocabulary. The slot id stays in the aria-label so
            // assistive tech (and tests) still identify the exact field.
            placeholder={t("apikeys_view.paste_key_placeholder")}
            className={cn(
              "w-full rounded-md border border-input bg-background px-3 py-1.5 pr-9 font-mono text-xs",
              "focus:outline-none focus:ring-1 focus:ring-primary",
            )}
            onKeyDown={(e) => {
              if (e.key === "Enter") void handleSave();
            }}
          />
          <button
            type="button"
            onClick={() => setReveal((r) => !r)}
            className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
            aria-label={t(reveal ? "apikeys_view.hide_key" : "apikeys_view.reveal_key")}
            title={t(reveal ? "apikeys_view.hide_key" : "apikeys_view.reveal_key")}
          >
            {reveal ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
          </button>
        </div>
        <Button size="sm" onClick={handleSave} disabled={pending || !value.trim()}>
          {pending ? "…" : t("common.save")}
        </Button>
        {(configured || coveredByShared) && (
          <Button size="sm" variant="ghost" onClick={() => setEditing(false)}>
            {t("common.cancel")}
          </Button>
        )}
      </div>
      {fmt && !fmt.match && fmt.detected && (
        <div className="space-y-1">
          <p className="flex items-start gap-1 text-[11px] text-amber-500">
            <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />
            <span>
              {t("apikeys_view.format_mismatch").replace("{0}", fmt.detected.label)}
            </span>
          </p>
          {/* The note explains WHAT the pasted thing actually is (e.g. "a
              Vertex service-account file, not an AI Studio key") — most useful
              exactly here, in the mismatch case, not only on a match. */}
          {fmt.detected.note && (
            <p className="text-[11px] text-muted-foreground">{fmt.detected.note}</p>
          )}
        </div>
      )}
      {/* Positive reassurance, only when the pasted value confidently matches
          the format this slot expects. A non-technical user pasting a long
          random string has no way to tell "right kind of key" from "garbage" —
          this answers it before they hit Save. Format only, never validity. */}
      {fmt && fmt.match && fmt.expected && fmt.detected?.kind === fmt.expected && (
        <p className="flex items-start gap-1 text-[11px] text-emerald-600 dark:text-emerald-400">
          <CheckCircle2 className="mt-0.5 h-3 w-3 shrink-0" />
          <span>{t("apikeys_view.format_match_hint")}</span>
        </p>
      )}
      {fmt && fmt.match && fmt.detected?.note && (
        <p className="text-[11px] text-muted-foreground">{fmt.detected.note}</p>
      )}
      {dashboardLink}
    </div>
  );
}
