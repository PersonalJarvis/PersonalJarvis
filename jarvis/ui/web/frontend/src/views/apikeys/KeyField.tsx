import { useState } from "react";
import { AlertTriangle, ExternalLink, Eye, EyeOff, Loader2, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { deleteSecret, postSecret } from "@/hooks/useProviders";
import { useT } from "@/i18n";
import { keyMatchesSecret } from "@/lib/keyFormat";
import { openExternalUrl } from "@/lib/openExternal";
import { useEventStore } from "@/store/events";
import { Row } from "./ledger";

function announce(slot: string, action: "set" | "delete") {
  // Every mounted list and the health dots re-read. Only the slot NAME
  // travels, never a value.
  window.dispatchEvent(new CustomEvent("jarvis:secret-configured", { detail: { key: slot, action } }));
}

/**
 * One API key as one settings row. Saving always uses the "everywhere"
 * scope: the key becomes the company key, so every feature of that company
 * reads it. A saved key shows as saved, with Replace and Remove; an empty one
 * shows the field itself.
 */
export function KeyField({
  slot,
  present,
  providerLabel,
  dashboardUrl,
  title,
  description,
  onChanged,
  testId = "provider-key",
  stacked = false,
}: {
  slot: string;
  present: boolean;
  providerLabel: string;
  dashboardUrl?: string | null;
  title?: string;
  description?: string;
  onChanged?: () => void | Promise<void>;
  testId?: string;
  /** Field under the text, full width — for a narrow column. */
  stacked?: boolean;
}) {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState("");
  const [reveal, setReveal] = useState(false);
  const [busy, setBusy] = useState<"save" | "delete" | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const showInput = !present || editing;
  const format = value.trim() ? keyMatchesSecret(slot, value) : null;

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
      pushToast("success", t("providers_page.key_saved_toast").replace("{0}", providerLabel));
      await onChanged?.();
    } catch (cause) {
      pushToast("error", (cause as Error).message);
    } finally {
      setBusy(null);
    }
  }

  async function remove() {
    setBusy("delete");
    try {
      await deleteSecret(slot);
      announce(slot, "delete");
      pushToast("info", t("providers_page.key_removed_toast").replace("{0}", providerLabel));
      await onChanged?.();
    } catch (cause) {
      pushToast("error", (cause as Error).message);
    } finally {
      setBusy(null);
      setConfirmDelete(false);
    }
  }

  const getKey = dashboardUrl ? (
    <button
      type="button"
      onClick={() => void openExternalUrl(dashboardUrl)}
      className="inline-flex items-center gap-1 text-xs text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
    >
      {t("providers_page.get_key")}
      <ExternalLink aria-hidden="true" className="h-3 w-3" />
    </button>
  ) : null;

  const control = showInput ? (
    <div className={stacked ? "flex w-full items-center gap-2" : "flex w-full items-center gap-2 sm:w-auto"}>
      <div className={stacked ? "relative min-w-0 flex-1" : "relative min-w-0 flex-1 sm:w-72 sm:flex-none"}>
        <Input
          type={reveal ? "text" : "password"}
          autoComplete="off"
          spellCheck={false}
          data-testid={`${testId}-input`}
          aria-label={title ?? t("providers_page.key_label")}
          placeholder={t("providers_page.key_placeholder").replace("{0}", providerLabel)}
          value={value}
          onChange={(event) => setValue(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") void save();
            if (event.key === "Escape" && present) {
              setEditing(false);
              setValue("");
            }
          }}
          className="h-8 pr-9 font-mono text-sm"
        />
        <button
          type="button"
          onClick={() => setReveal((on) => !on)}
          aria-label={t(reveal ? "providers_page.key_hide" : "providers_page.key_show")}
          className="absolute right-1 top-1/2 grid h-6 w-6 -translate-y-1/2 place-items-center rounded text-muted-foreground hover:text-foreground"
        >
          {reveal ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
        </button>
      </div>
      {editing && (
        <Button size="sm" variant="ghost" onClick={() => { setEditing(false); setValue(""); }}>
          {t("common.cancel")}
        </Button>
      )}
      <Button size="sm" data-testid={`${testId}-save`} disabled={!value.trim() || busy !== null} onClick={() => void save()}>
        {busy === "save" && <Loader2 className="animate-spin" />}
        {t("providers_page.key_save")}
      </Button>
    </div>
  ) : confirmDelete ? (
    <>
      <Button size="sm" variant="ghost" onClick={() => setConfirmDelete(false)}>
        {t("common.cancel")}
      </Button>
      <Button size="sm" variant="destructive" data-testid={`${testId}-remove-confirm`} disabled={busy !== null} onClick={() => void remove()}>
        {busy === "delete" && <Loader2 className="animate-spin" />}
        {t("providers_page.key_remove")}
      </Button>
    </>
  ) : (
    <>
      <Button size="sm" variant="outline" data-testid={`${testId}-replace`} onClick={() => setEditing(true)}>
        {t("providers_page.key_replace")}
      </Button>
      <Button
        size="icon"
        variant="ghost"
        className="h-8 w-8 text-muted-foreground"
        aria-label={t("providers_page.key_remove")}
        title={t("providers_page.key_remove")}
        data-testid={`${testId}-remove`}
        onClick={() => setConfirmDelete(true)}
      >
        <Trash2 />
      </Button>
    </>
  );

  const statusNode = (
    <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
      <span className="inline-flex items-center gap-1.5">
        <span aria-hidden="true" className={present ? "h-1.5 w-1.5 rounded-full bg-success" : "h-1.5 w-1.5 rounded-full bg-border-strong"} />
        {confirmDelete
          ? t("providers_page.key_remove_confirm")
          : present
            ? t("providers_page.key_saved_hint")
            : t("providers_page.key_missing_hint")}
      </span>
      {getKey}
      {format && !format.match && format.detected && (
        <span role="status" className="inline-flex items-center gap-1 text-warning">
          <AlertTriangle aria-hidden="true" className="h-3.5 w-3.5" />
          {t("providers_page.key_format_warning")
            .replace("{0}", format.detected.label)
            .replace("{1}", providerLabel)}
        </span>
      )}
    </span>
  );

  return (
    <Row
      data-testid={testId}
      stacked={stacked}
      title={title ?? t("providers_page.key_label")}
      description={
        <>
          {description ? <span className="block">{description}</span> : null}
          <span className={description ? "mt-1.5 block" : "block"}>{statusNode}</span>
        </>
      }
      control={control}
    />
  );
}
