/**
 * One entry in the file — the value, where it came from, and the editor.
 *
 * The disclosure is the point of the whole section. Every fact the assistant
 * holds was written by one of three writers, each of which appends an audit
 * line to USER.md saying what it wrote and which sentence it took it from
 * (see provenance.ts). Opening a row shows that sentence. It is the only
 * honest answer to "how do you know that about me?", and until now it was on
 * disk with nothing reading it.
 *
 * The three editor shapes — text, a yes/no pair, removable chips — are lifted
 * from the old KnowledgeLedger unchanged. They were right: the shape follows
 * the field kind, and the field kind is pinned against the backend's
 * _LIST_FIELDS / _BOOL_FIELDS by a parity test.
 */
import { useEffect, useRef, useState } from "react";
import { Check, ChevronDown, Pencil, Plus, Quote, Trash2, X } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { useEventStore } from "@/store/events";
import { renderValue, useFieldEdit, type FieldOp } from "@/views/profile/api";
import { fieldKind, isEmptyValue, type ClusterId } from "@/views/profile/ledger";
import type { Observation } from "@/views/profile/provenance";

/** A 32 px square icon control, sized for a 40 px row. */
function RowIconButton({
  icon: Icon,
  onClick,
  title,
  variant = "ghost",
  disabled,
}: {
  icon: typeof Check;
  onClick: () => void;
  title: string;
  variant?: "default" | "ghost" | "outline";
  disabled?: boolean;
}) {
  return (
    <Button
      type="button"
      variant={variant}
      size="icon"
      className="h-8 w-8 shrink-0"
      onClick={onClick}
      title={title}
      aria-label={title}
      disabled={disabled}
    >
      <Icon />
    </Button>
  );
}

export function EntryRow({
  cid,
  fieldKey,
  value,
  latest,
  history,
}: {
  cid: ClusterId;
  fieldKey: string;
  value: unknown;
  /** The audit line that explains the value on screen, if there is one. */
  latest: Observation | null;
  /** Every audit line for this field, oldest first. */
  history: readonly Observation[];
}) {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);
  const edit = useFieldEdit();
  const kind = fieldKind(fieldKey);
  const empty = isEmptyValue(value);
  const label = t(`profile_view.fields.${fieldKey}`);

  const [editing, setEditing] = useState(false);
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const inputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    if (editing) inputRef.current?.focus();
  }, [editing]);

  const busy = edit.isPending;

  const startEdit = () => {
    setDraft(kind === "scalar" && !empty ? String(value) : "");
    setEditing(true);
  };
  const cancel = () => {
    setDraft("");
    setEditing(false);
  };
  const mutate = (
    operation: FieldOp,
    v?: unknown,
    opts?: { keepOpen?: boolean; clearDraft?: boolean },
  ) => {
    edit.mutate(
      { cluster: cid, field: fieldKey, operation, value: v },
      {
        onSuccess: () => {
          pushToast("success", t("profile_view.field_saved"));
          if (opts?.clearDraft) setDraft("");
          if (opts?.keepOpen) inputRef.current?.focus();
          else cancel();
        },
      },
    );
  };

  const saveScalar = () => {
    const v = draft.trim();
    if (v) mutate("set", v);
    else mutate("clear");
  };
  const addItem = () => {
    const v = draft.trim();
    if (v) mutate("append", v, { keepOpen: true, clearDraft: true });
  };

  // ------------------------------------------------------------------ editing
  if (editing) {
    return (
      <div className="-mx-2 rounded-md bg-secondary px-2 py-2">
        <p className="text-body font-medium text-foreground-strong">{label}</p>
        <div className="mt-2">
          {kind === "bool" ? (
            <div className="flex flex-wrap items-center gap-2">
              {([true, false] as const).map((b) => (
                <Button
                  key={String(b)}
                  type="button"
                  size="sm"
                  variant={value === b ? "default" : "outline"}
                  disabled={busy}
                  onClick={() => mutate("set", b)}
                >
                  {b ? t("profile_view.value_yes") : t("profile_view.value_no")}
                </Button>
              ))}
              <span className="ml-auto flex items-center gap-1">
                <RowIconButton
                  icon={X}
                  onClick={cancel}
                  title={t("profile_view.raw_cancel")}
                  disabled={busy}
                />
                {!empty && (
                  <RowIconButton
                    icon={Trash2}
                    onClick={() => mutate("clear")}
                    title={t("profile_view.field_clear")}
                    disabled={busy}
                  />
                )}
              </span>
            </div>
          ) : kind === "list" ? (
            <div className="flex flex-col gap-2">
              {!empty && (
                <div className="flex flex-wrap gap-1">
                  {(value as unknown[]).map((item) => (
                    <Badge key={String(item)} variant="outline" className="pr-1 text-foreground">
                      {String(item)}
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() => mutate("remove", String(item), { keepOpen: true })}
                        title={t("profile_view.field_remove_item")}
                        aria-label={`${t("profile_view.field_remove_item")}: ${String(item)}`}
                        className="rounded-sm p-0.5 text-muted-foreground transition-colors hover:text-destructive focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
                      >
                        <X aria-hidden className="h-3 w-3" />
                      </button>
                    </Badge>
                  ))}
                </div>
              )}
              <div className="flex items-center gap-1">
                <Input
                  ref={inputRef}
                  value={draft}
                  disabled={busy}
                  onChange={(e) => setDraft(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") addItem();
                    if (e.key === "Escape") cancel();
                  }}
                  placeholder={t("profile_view.field_add_placeholder")}
                  className="h-8"
                />
                <RowIconButton
                  icon={Plus}
                  onClick={addItem}
                  title={t("profile_view.field_add")}
                  variant="default"
                  disabled={busy}
                />
                <RowIconButton
                  icon={Check}
                  onClick={cancel}
                  title={t("profile_view.raw_save")}
                  disabled={busy}
                />
                {!empty && (
                  <RowIconButton
                    icon={Trash2}
                    onClick={() => mutate("clear")}
                    title={t("profile_view.field_clear")}
                    disabled={busy}
                  />
                )}
              </div>
            </div>
          ) : (
            <div className="flex items-center gap-1">
              <Input
                ref={inputRef}
                value={draft}
                disabled={busy}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") saveScalar();
                  if (e.key === "Escape") cancel();
                }}
                placeholder={t("profile_view.field_value_placeholder")}
                className="h-8"
              />
              <RowIconButton
                icon={Check}
                onClick={saveScalar}
                title={t("profile_view.raw_save")}
                variant="default"
                disabled={busy}
              />
              <RowIconButton
                icon={X}
                onClick={cancel}
                title={t("profile_view.raw_cancel")}
                disabled={busy}
              />
              {!empty && (
                <RowIconButton
                  icon={Trash2}
                  onClick={() => mutate("clear")}
                  title={t("profile_view.field_clear")}
                  disabled={busy}
                />
              )}
            </div>
          )}
        </div>
      </div>
    );
  }

  // ------------------------------------------------------------------ resting
  const hasSource = latest !== null;

  return (
    <div className="-mx-2">
      {/* A file reads in columns: the field name in a fixed measure, the value
          set immediately beside it. Pushing the value to the far edge of a
          1200 px card left the two ends of a fact a hand-span apart. */}
      <div className="group grid min-h-10 grid-cols-[minmax(7rem,11rem)_minmax(0,1fr)_auto] items-center gap-3 rounded-md px-2 py-1 transition-colors hover:bg-secondary">
        <span className="truncate text-body text-muted-foreground" title={label}>
          {label}
        </span>

        <span className="flex min-w-0 items-center gap-2">
          {kind === "list" && !empty ? (
            <span className="flex min-w-0 flex-wrap gap-1">
              {(value as unknown[]).map((item) => (
                <Badge key={String(item)} variant="secondary" className="text-foreground">
                  {String(item)}
                </Badge>
              ))}
            </span>
          ) : empty ? (
            <span className="text-body text-foreground-faint">
              {t("profile_view.field_unknown")}
            </span>
          ) : (
            <span className="text-body text-foreground [overflow-wrap:anywhere]">
              {renderValue(t, value)}
            </span>
          )}
        </span>

        <span className="flex shrink-0 items-center gap-1">
          {/* The signature: the date is a button, and the button is the file's
              receipt for this line. No provenance means no marker — never an
              invented one for a fact that predates the audit trail. */}
          {hasSource && (
            <button
              type="button"
              onClick={() => setOpen((v) => !v)}
              aria-expanded={open}
              data-testid={`entry-source-${fieldKey}`}
              title={t("profile_view.source_show")}
              className="flex shrink-0 items-center gap-0.5 rounded-sm px-1 font-mono text-micro tabular-nums text-foreground-faint transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              {latest.date}
              <ChevronDown
                aria-hidden
                className={cn("h-3 w-3 transition-transform", open && "rotate-180")}
              />
            </button>
          )}

          <Button
            type="button"
            variant="ghost"
            size="icon"
            onClick={startEdit}
            title={t("profile_view.field_edit")}
            aria-label={`${t("profile_view.field_edit")}: ${label}`}
            className="h-8 w-8 shrink-0 text-muted-foreground opacity-0 transition-opacity focus-visible:opacity-100 group-hover:opacity-100"
          >
            {empty ? <Plus /> : <Pencil />}
          </Button>
        </span>
      </div>

      {open && hasSource && (
        <div className="mb-1 ml-2 mr-2 rounded-md border-l-2 border-border-strong bg-secondary/60 px-3 py-2">
          {latest.evidence ? (
            <p className="flex gap-2 text-meta italic text-foreground">
              <Quote aria-hidden className="mt-0.5 h-3 w-3 shrink-0 text-muted-foreground" />
              <span className="[overflow-wrap:anywhere]">{latest.evidence}</span>
            </p>
          ) : (
            <p className="text-meta text-muted-foreground">{t("profile_view.source_no_quote")}</p>
          )}
          <p className="mt-1.5 font-mono text-micro text-foreground-faint">
            {t("profile_view.source_learned").replace("{0}", latest.date)}
            {history.length > 1 &&
              ` · ${t("profile_view.source_revisions").replace("{0}", String(history.length))}`}
          </p>
        </div>
      )}
    </div>
  );
}
