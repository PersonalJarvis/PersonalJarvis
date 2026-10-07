/**
 * Add or edit a terminal CLI of your own.
 *
 * The form behind "this app should also be able to open X" — where X is any
 * interactive CLI this build has never heard of. Four fields, because that is
 * everything the backend needs to treat it exactly like a shipped one: a name,
 * the command that starts it, a line of description for the picker, and a logo.
 *
 * ## Why the command is checked here at all
 *
 * The server validates it too, and its answer is the one that counts. What this
 * side adds is the reading the server cannot give in time: a command containing
 * a pipe, a `&&` or a `VAR=x` prefix is shell source, so the pane will run a
 * shell around it — which changes what "the terminal exited" means later. Saying
 * that WHILE the user types beats discovering it when a pane closes unexpectedly
 * three days on.
 *
 * ## Why the logo is uploaded separately from the fields
 *
 * An entry has to exist before it can own a file, and the id that file is named
 * after is assigned on creation. So a new CLI is saved first and its logo lands
 * immediately after — one save from the user's side, two requests underneath. A
 * failed logo upload therefore reports itself without throwing away a perfectly
 * good entry the user just typed.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { AlertTriangle, ImagePlus, Info, Loader2, Terminal, Trash2, X } from "lucide-react";

import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { Switch } from "@/components/ui/switch";
import {
  createCustomCli,
  removeCustomCliLogo,
  updateCustomCli,
  uploadCustomCliLogo,
  type CustomCli,
} from "@/lib/workspaceClisApi";
import { AgentMark } from "./AgentMark";
import { Button } from "./controls";

/** Shell characters that mean the command is source rather than an argv. */
const SHELL_META = /[|&;<>()$`\n]/;
const ASSIGNMENT = /^[A-Za-z_][A-Za-z0-9_]*=/;

/** Mirrors `custom_clis.needs_shell` — see this file's header for why. */
export function runsThroughShell(command: string): boolean {
  if (SHELL_META.test(command)) return true;
  const first = command.trim().split(/\s+/)[0] ?? "";
  return ASSIGNMENT.test(first);
}

function message(reason: unknown): string {
  return reason instanceof Error ? reason.message : String(reason);
}

export function CustomCliDialog({
  open,
  onOpenChange,
  /** The entry being edited, or null to add a new one. */
  editing,
  onSaved,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  editing?: CustomCli | null;
  onSaved: (entry: CustomCli) => void;
}): JSX.Element {
  const t = useT();
  const [name, setName] = useState("");
  const [command, setCommand] = useState("");
  const [description, setDescription] = useState("");
  const [atReference, setAtReference] = useState(false);
  const [logoFile, setLogoFile] = useState<File | null>(null);
  const [logoPreview, setLogoPreview] = useState("");
  const [clearedLogo, setClearedLogo] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const pickerRef = useRef<HTMLInputElement | null>(null);

  /*
   * Refill from the entry every time the dialog opens.
   *
   * Keyed on `open` as well as on the entry, because the same instance is
   * reused for "edit A", then "add new", then "edit A again": without the
   * reopen in the dependency list the second visit to A would still show
   * whatever was half-typed during the "add new" that came between.
   */
  useEffect(() => {
    if (!open) return;
    setName(editing?.display_name ?? "");
    setCommand(editing?.command ?? "");
    setDescription(editing?.description ?? "");
    setAtReference(editing?.file_reference === "at");
    setLogoFile(null);
    setClearedLogo(false);
    setError("");
  }, [open, editing]);

  /* A local preview of the picked file, revoked when it is replaced. */
  useEffect(() => {
    if (!logoFile) {
      setLogoPreview("");
      return;
    }
    const url = URL.createObjectURL(logoFile);
    setLogoPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [logoFile]);

  const trimmedName = name.trim();
  const trimmedCommand = command.trim();
  const canSave = Boolean(trimmedName && trimmedCommand) && !saving;
  const throughShell = useMemo(
    () => Boolean(trimmedCommand) && runsThroughShell(trimmedCommand),
    [trimmedCommand],
  );
  const shownLogo = clearedLogo
    ? ""
    : logoPreview || editing?.logo_url || "";

  const save = async () => {
    if (!canSave) return;
    setSaving(true);
    setError("");
    try {
      const draft = {
        display_name: trimmedName,
        command: trimmedCommand,
        description: description.trim(),
        file_reference: atReference ? ("at" as const) : ("quoted" as const),
      };
      let entry = editing
        ? await updateCustomCli(editing.id, draft)
        : await createCustomCli(draft);
      // The entry is stored either way by this point. A logo that fails from
      // here on is reported as exactly that, rather than as a failed save.
      if (logoFile) entry = await uploadCustomCliLogo(entry.id, logoFile);
      else if (clearedLogo && editing?.logo_url) {
        entry = await removeCustomCliLogo(entry.id);
      }
      onSaved(entry);
      onOpenChange(false);
    } catch (reason: unknown) {
      setError(message(reason));
    } finally {
      setSaving(false);
    }
  };

  const inputClass =
    "h-9 w-full min-w-0 rounded-md border border-border bg-background px-3 text-sm text-foreground " +
    "outline-none transition-colors placeholder:text-faint-foreground " +
    "hover:border-border-strong focus:border-border-strong focus-visible:ring-2 focus-visible:ring-ring/40";

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-[80] bg-scrim/70 backdrop-blur-sm data-[state=open]:animate-in data-[state=open]:fade-in-0 motion-reduce:animate-none" />
        <Dialog.Content
          data-testid="custom-cli-dialog"
          className={cn(
            "fixed left-1/2 top-1/2 z-[90] flex max-h-[min(88dvh,46rem)] w-[min(520px,calc(100vw-2rem))]",
            "-translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden rounded-xl border border-border",
            "bg-popover text-foreground shadow-float outline-none",
            "data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95 motion-reduce:animate-none",
          )}
        >
          <header className="flex items-start gap-3 px-6 pb-4 pt-5">
            <span
              aria-hidden="true"
              className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-border bg-secondary text-foreground"
            >
              <Terminal className="h-4 w-4" />
            </span>
            <div className="min-w-0 flex-1 pt-0.5">
              <Dialog.Title className="font-display text-base font-semibold tracking-tight text-foreground-strong">
                {editing ? t("custom_cli.title_edit") : t("custom_cli.title_add")}
              </Dialog.Title>
              <Dialog.Description className="mt-1 text-sm leading-relaxed text-muted-foreground">
                {t("custom_cli.description")}
              </Dialog.Description>
            </div>
            <Dialog.Close asChild>
              <button
                type="button"
                aria-label={t("custom_cli.close")}
                className="-mr-2 -mt-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
              >
                <X className="h-4 w-4" aria-hidden="true" />
              </button>
            </Dialog.Close>
          </header>

          <div className="flex min-h-0 flex-1 flex-col gap-5 overflow-y-auto border-t border-border px-6 py-5 scrollbar-jarvis">
            {/* Identity: the mark and the name side by side, because the mark is
                what the name will be seen next to in every picker. The whole
                mark is the upload target, so there is no separate tiny icon. */}
            <div className="flex items-start gap-4">
              <div className="flex w-16 shrink-0 flex-col items-center gap-1.5">
                <button
                  type="button"
                  onClick={() => pickerRef.current?.click()}
                  aria-label={t("custom_cli.pick_logo")}
                  title={t("custom_cli.pick_logo")}
                  data-testid="custom-cli-pick-logo"
                  className={cn(
                    "group relative flex h-16 w-16 items-center justify-center overflow-hidden rounded-xl transition-colors",
                    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60",
                    shownLogo || trimmedName
                      ? "border border-border bg-background hover:border-border-strong"
                      : "border border-dashed border-border-strong bg-background/60 text-muted-foreground hover:bg-secondary hover:text-foreground",
                  )}
                >
                  {shownLogo || trimmedName ? (
                    <AgentMark
                      agent={editing?.id ?? "new"}
                      label={trimmedName || "?"}
                      size="lg"
                      logoUrl={shownLogo || undefined}
                      // The tile is the frame; the mark fills it rather than
                      // drawing a second box inside.
                      className="h-full w-full rounded-none border-0 bg-transparent text-base"
                    />
                  ) : (
                    <ImagePlus className="h-5 w-5" aria-hidden="true" />
                  )}
                  {(shownLogo || trimmedName) && (
                    <span
                      aria-hidden="true"
                      className="absolute inset-0 flex items-center justify-center bg-scrim/55 text-white opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100"
                    >
                      <ImagePlus className="h-4 w-4" />
                    </span>
                  )}
                </button>
                {shownLogo ? (
                  <button
                    type="button"
                    onClick={() => {
                      setLogoFile(null);
                      setClearedLogo(true);
                    }}
                    title={t("custom_cli.remove_logo")}
                    className="inline-flex items-center gap-1 rounded px-1 text-micro text-muted-foreground transition-colors hover:text-destructive focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
                  >
                    <Trash2 className="h-3 w-3" aria-hidden="true" />
                    {t("custom_cli.remove_logo_short")}
                  </button>
                ) : (
                  <span className="text-micro text-faint-foreground">{t("custom_cli.logo_label")}</span>
                )}
                <input
                  ref={pickerRef}
                  type="file"
                  accept=".svg,.png,.jpg,.jpeg,.webp,.gif,image/*"
                  className="hidden"
                  data-testid="custom-cli-logo-input"
                  onChange={(event) => {
                    const picked = event.currentTarget.files?.[0] ?? null;
                    if (picked) {
                      setLogoFile(picked);
                      setClearedLogo(false);
                    }
                    // Cleared so picking the SAME file twice still fires.
                    event.currentTarget.value = "";
                  }}
                />
              </div>

              <label className="flex min-w-0 flex-1 flex-col gap-1.5">
                <span className="text-xs font-medium text-foreground">{t("custom_cli.name")}</span>
                <input
                  value={name}
                  autoFocus
                  maxLength={60}
                  data-testid="custom-cli-name"
                  placeholder={t("custom_cli.name_placeholder")}
                  className={inputClass}
                  onChange={(event) => setName(event.currentTarget.value)}
                />
                <span className="text-xs leading-relaxed text-muted-foreground">
                  {t("custom_cli.name_hint")}
                </span>
              </label>
            </div>

            <label className="flex flex-col gap-1.5">
              <span className="text-xs font-medium text-foreground">{t("custom_cli.command")}</span>
              {/* A prompt sign in front of the command reads as "typed into a
                  terminal", which is exactly what this field holds. */}
              <span className="relative block">
                <span
                  aria-hidden="true"
                  className="pointer-events-none absolute inset-y-0 left-3 flex items-center font-mono text-sm text-faint-foreground"
                >
                  $
                </span>
                <input
                  value={command}
                  maxLength={500}
                  spellCheck={false}
                  autoCapitalize="off"
                  autoCorrect="off"
                  data-testid="custom-cli-command"
                  placeholder={t("custom_cli.command_placeholder")}
                  className={cn(inputClass, "pl-7 font-mono")}
                  onChange={(event) => setCommand(event.currentTarget.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" && canSave) void save();
                  }}
                />
              </span>
              {throughShell ? (
                <span className="flex items-start gap-2 rounded-md bg-secondary px-3 py-2 text-xs leading-relaxed text-foreground">
                  <Info className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
                  {t("custom_cli.command_shell_hint")}
                </span>
              ) : (
                <span className="text-xs leading-relaxed text-muted-foreground">
                  {t("custom_cli.command_hint")}
                </span>
              )}
            </label>

            <label className="flex flex-col gap-1.5">
              <span className="flex items-baseline justify-between gap-2 text-xs font-medium text-foreground">
                {t("custom_cli.description_label")}
                <span className="font-normal text-faint-foreground">{t("custom_cli.optional")}</span>
              </span>
              <input
                value={description}
                maxLength={200}
                data-testid="custom-cli-description"
                placeholder={t("custom_cli.description_placeholder")}
                className={inputClass}
                onChange={(event) => setDescription(event.currentTarget.value)}
              />
            </label>

            <div className="flex items-start justify-between gap-4 rounded-lg border border-border bg-background/60 px-4 py-3">
              <label htmlFor="custom-cli-at-reference" className="min-w-0 cursor-pointer">
                <span className="block text-sm font-medium text-foreground">
                  {t("custom_cli.at_reference")}
                </span>
                <span className="mt-0.5 block text-xs leading-relaxed text-muted-foreground">
                  {t("custom_cli.at_reference_hint")}
                </span>
              </label>
              <Switch
                id="custom-cli-at-reference"
                checked={atReference}
                data-testid="custom-cli-at-reference"
                onCheckedChange={setAtReference}
                className="mt-0.5"
              />
            </div>

            {error && (
              <p
                role="alert"
                data-testid="custom-cli-error"
                className="flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive"
              >
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
                <span className="min-w-0">{error}</span>
              </p>
            )}
          </div>

          <footer className="flex shrink-0 items-center justify-end gap-2 border-t border-border bg-background/40 px-6 py-4">
            <Dialog.Close asChild>
              <Button variant="quiet" className="h-9 px-4">{t("custom_cli.cancel")}</Button>
            </Dialog.Close>
            <Button
              variant="primary"
              disabled={!canSave}
              data-testid="custom-cli-save"
              className="h-9 px-4"
              onClick={() => void save()}
            >
              {saving && <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />}
              {saving
                ? t("custom_cli.saving")
                : editing
                  ? t("custom_cli.save")
                  : t("custom_cli.save_add")}
            </Button>
          </footer>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
