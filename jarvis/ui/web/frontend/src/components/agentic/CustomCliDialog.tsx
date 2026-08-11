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
import { ImagePlus, Terminal, Trash2, X } from "lucide-react";

import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import {
  createCustomCli,
  removeCustomCliLogo,
  updateCustomCli,
  uploadCustomCliLogo,
  type CustomCli,
} from "@/lib/workspaceClisApi";
import { AgentMark } from "./AgentMark";
import { Button, Field, SectionLabel } from "./controls";

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

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-[80] bg-[#090909]/75 backdrop-blur-sm data-[state=open]:animate-in data-[state=open]:fade-in-0 motion-reduce:animate-none" />
        <Dialog.Content
          data-testid="custom-cli-dialog"
          className={cn(
            "fixed left-1/2 top-1/2 z-[90] flex max-h-[min(88dvh,44rem)] w-[min(560px,calc(100vw-2rem))]",
            "-translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden rounded-2xl border border-border",
            "bg-card shadow-[0_28px_90px_-24px_rgba(0,0,0,0.75)] outline-none",
            "data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95 motion-reduce:animate-none",
          )}
        >
          <header className="flex items-start justify-between gap-4 border-b border-border px-5 py-4">
            <div className="min-w-0">
              <div className="mb-1 flex items-center gap-2">
                <Terminal className="h-4 w-4 text-primary" aria-hidden="true" />
                <Dialog.Title className="font-display text-base font-semibold tracking-tight text-foreground">
                  {editing
                    ? t("custom_cli.title_edit")
                    : t("custom_cli.title_add")}
                </Dialog.Title>
              </div>
              <Dialog.Description className="text-xs leading-relaxed text-muted-foreground">
                {t("custom_cli.description")}
              </Dialog.Description>
            </div>
            <Dialog.Close asChild>
              <button
                type="button"
                aria-label={t("custom_cli.close")}
                className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
              >
                <X className="h-4 w-4" aria-hidden="true" />
              </button>
            </Dialog.Close>
          </header>

          <div className="flex min-h-0 flex-1 flex-col gap-5 overflow-y-auto px-5 py-5 scrollbar-jarvis">
            {/* Identity: the mark and the name, side by side, because the mark
                is what the name will be seen next to in every picker. */}
            <div className="flex items-start gap-4">
              <div className="flex shrink-0 flex-col items-center gap-1.5">
                <AgentMark
                  agent={editing?.id ?? "new"}
                  label={trimmedName || "?"}
                  size="lg"
                  logoUrl={shownLogo || undefined}
                />
                <div className="flex items-center gap-0.5">
                  <button
                    type="button"
                    onClick={() => pickerRef.current?.click()}
                    aria-label={t("custom_cli.pick_logo")}
                    title={t("custom_cli.pick_logo")}
                    data-testid="custom-cli-pick-logo"
                    className="flex h-6 w-6 items-center justify-center rounded-control text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground"
                  >
                    <ImagePlus className="h-3.5 w-3.5" aria-hidden="true" />
                  </button>
                  {shownLogo && (
                    <button
                      type="button"
                      onClick={() => {
                        setLogoFile(null);
                        setClearedLogo(true);
                      }}
                      aria-label={t("custom_cli.remove_logo")}
                      title={t("custom_cli.remove_logo")}
                      className="flex h-6 w-6 items-center justify-center rounded-control text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground"
                    >
                      <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                    </button>
                  )}
                </div>
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
                <SectionLabel>{t("custom_cli.name")}</SectionLabel>
                <Field
                  value={name}
                  autoFocus
                  maxLength={60}
                  data-testid="custom-cli-name"
                  placeholder={t("custom_cli.name_placeholder")}
                  onChange={(event) => setName(event.currentTarget.value)}
                />
                <span className="text-[11px] leading-relaxed text-muted-foreground">
                  {t("custom_cli.name_hint")}
                </span>
              </label>
            </div>

            <label className="flex flex-col gap-1.5">
              <SectionLabel>{t("custom_cli.command")}</SectionLabel>
              <Field
                value={command}
                maxLength={500}
                spellCheck={false}
                autoCapitalize="off"
                autoCorrect="off"
                data-testid="custom-cli-command"
                placeholder={t("custom_cli.command_placeholder")}
                className="font-mono"
                onChange={(event) => setCommand(event.currentTarget.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && canSave) void save();
                }}
              />
              <span className="text-[11px] leading-relaxed text-muted-foreground">
                {throughShell
                  ? t("custom_cli.command_shell_hint")
                  : t("custom_cli.command_hint")}
              </span>
            </label>

            <label className="flex flex-col gap-1.5">
              <SectionLabel>{t("custom_cli.description_label")}</SectionLabel>
              <Field
                value={description}
                maxLength={200}
                data-testid="custom-cli-description"
                placeholder={t("custom_cli.description_placeholder")}
                onChange={(event) => setDescription(event.currentTarget.value)}
              />
            </label>

            <label className="flex cursor-pointer items-start gap-2.5">
              <input
                type="checkbox"
                checked={atReference}
                data-testid="custom-cli-at-reference"
                onChange={(event) => setAtReference(event.currentTarget.checked)}
                className="mt-0.5 h-4 w-4 shrink-0 accent-primary"
              />
              <span className="min-w-0">
                <span className="block text-sm text-foreground">
                  {t("custom_cli.at_reference")}
                </span>
                <span className="block text-[11px] leading-relaxed text-muted-foreground">
                  {t("custom_cli.at_reference_hint")}
                </span>
              </span>
            </label>

            {error && (
              <p
                role="alert"
                data-testid="custom-cli-error"
                className="border-l-2 border-destructive/70 py-1 pl-3 text-sm text-destructive"
              >
                {error}
              </p>
            )}
          </div>

          <footer className="flex shrink-0 items-center justify-end gap-2 border-t border-border px-5 py-4">
            <Dialog.Close asChild>
              <Button variant="quiet">{t("custom_cli.cancel")}</Button>
            </Dialog.Close>
            <Button
              variant="primary"
              disabled={!canSave}
              data-testid="custom-cli-save"
              onClick={() => void save()}
            >
              {saving ? t("custom_cli.saving") : t("custom_cli.save")}
            </Button>
          </footer>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
