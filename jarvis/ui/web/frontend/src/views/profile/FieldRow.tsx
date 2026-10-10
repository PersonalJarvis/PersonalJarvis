/**
 * One profile detail: the value, where it came from, and its editor.
 *
 * Resting, a row is a label on the left and the value on the right — the
 * shape every settings list uses. Under the label sits the receipt: the date
 * the audit trail recorded the fact (see provenance.ts), and on click the
 * sentence it was taken from. A fact the trail never mentions shows no date
 * at all rather than an invented one.
 *
 * The editor follows the field's shape: a closed vocabulary is a segmented
 * control, a 1–5 scale is five steps, a yes/no is two buttons, a list is
 * removable chips plus suggestions, anything else is one text input. Picking
 * a choice saves at once; typed text saves on Enter. The shapes are pinned
 * against the backend's _LIST_FIELDS / _BOOL_FIELDS by a parity test.
 */
import { useEffect, useRef, useState } from "react";
import { Check, Pencil, Plus, Quote, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useI18nStore, useT, useUiLanguage } from "@/i18n";
import { localeForUiLanguage } from "@/components/runs/format";
import { cn } from "@/lib/utils";
import { useEventStore } from "@/store/events";
import { useFieldEdit, type FieldOp } from "@/views/profile/api";
import {
  CHOICE_FIELDS,
  LANGUAGE_FIELDS,
  LIST_SUGGESTIONS,
  SCALE_FIELDS,
  fieldKind,
  isEmptyValue,
  languageName,
  scaleValue,
  type ClusterId,
} from "@/views/profile/ledger";
import type { Observation } from "@/views/profile/provenance";

type T = (key: string) => string;

/** A YYYY-MM-DD stamp as a readable local date. */
export function readableDate(iso: string): string {
  const d = new Date(`${iso}T12:00:00`);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString(localeForUiLanguage(useI18nStore.getState().ui), { day: "numeric", month: "short", year: "numeric" });
}

/**
 * What a stored value shows: a choice by its label, a language code by its
 * name, anything else (including off-vocabulary curator text) verbatim.
 */
function choiceLabel(t: T, field: string, value: string, ui = "en"): string {
  if (CHOICE_FIELDS[field]?.includes(value) || LIST_CHOICE_FIELDS.has(field)) {
    const key = `profile_view.choices.${field}.${value}`;
    const label = t(key);
    if (label !== key) return label;
  }
  if (LANGUAGE_FIELDS.has(field)) return languageName(value, ui);
  return value;
}

/** List fields whose suggested items carry translated labels. */
const LIST_CHOICE_FIELDS: ReadonlySet<string> = new Set(Object.keys(LIST_SUGGESTIONS));

function ScaleMeter({ value }: { value: number }) {
  return (
    <span aria-hidden className="flex items-center gap-0.5">
      {[1, 2, 3, 4, 5].map((step) => (
        <span
          key={step}
          className={cn("h-2.5 w-1.5 rounded-full", step <= value ? "bg-accent" : "bg-foreground/15")}
        />
      ))}
    </span>
  );
}

/** The resting value, right-aligned in the row. */
function ValueView({ field, value }: { field: string; value: unknown }) {
  const t = useT();
  const ui = useUiLanguage();
  const kind = fieldKind(field);

  if (kind === "list") {
    return (
      <span className="flex flex-wrap justify-end gap-1">
        {(value as unknown[]).map((item) => (
          <span
            key={String(item)}
            className="rounded-md border border-border bg-secondary px-2 py-0.5 text-sm text-foreground"
          >
            {choiceLabel(t, field, String(item), ui)}
          </span>
        ))}
      </span>
    );
  }
  if (kind === "bool") {
    return (
      <span className="text-base text-foreground">
        {value ? t("profile_view.value_yes") : t("profile_view.value_no")}
      </span>
    );
  }
  if (SCALE_FIELDS.has(field)) {
    const n = scaleValue(value);
    if (n !== null) {
      return (
        <span className="flex items-center gap-2.5 text-base text-foreground">
          <ScaleMeter value={n} />
          <span className="font-mono text-sm tabular-nums">{n}/5</span>
        </span>
      );
    }
  }
  return (
    <span className="text-right text-base text-foreground [overflow-wrap:anywhere]">
      {choiceLabel(t, field, String(value), ui)}
    </span>
  );
}

/** One option in a segmented control. */
function Segment({
  active,
  disabled,
  onClick,
  children,
}: {
  active: boolean;
  disabled: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={active}
      disabled={disabled}
      onClick={onClick}
      className={cn(
        "min-w-10 flex-1 rounded-md px-3 py-1.5 text-sm font-medium transition-colors",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50",
        active
          ? "bg-card text-foreground-strong shadow-sm ring-1 ring-border"
          : "text-muted-foreground hover:text-foreground",
      )}
    >
      {children}
    </button>
  );
}

export function FieldRow({
  cid,
  field,
  value,
  latest,
  history,
  initiallyEditing = false,
  onClose,
}: {
  cid: ClusterId;
  field: string;
  value: unknown;
  /** The audit line that explains the value on screen, if there is one. */
  latest: Observation | null;
  /** Every audit line for this field, oldest first. */
  history: readonly Observation[];
  /** Open straight into the editor — used when a missing detail is added. */
  initiallyEditing?: boolean;
  /** Called when the editor closes. */
  onClose?: () => void;
}) {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);
  const edit = useFieldEdit();
  const kind = fieldKind(field);
  const empty = isEmptyValue(value);
  const label = t(`profile_view.fields.${field}`);
  // Free text only: choices, scales and yes/no save on click, not via Save.
  const typed = kind === "scalar" && !CHOICE_FIELDS[field] && !SCALE_FIELDS.has(field);

  const [editing, setEditing] = useState(initiallyEditing);
  const [showSource, setShowSource] = useState(false);
  const [draft, setDraft] = useState(kind === "scalar" && !empty ? String(value) : "");
  const inputRef = useRef<HTMLInputElement | null>(null);
  const busy = edit.isPending;

  useEffect(() => {
    if (editing) inputRef.current?.focus();
  }, [editing]);

  const open = () => {
    setDraft(kind === "scalar" && !empty ? String(value) : "");
    setEditing(true);
  };
  const close = () => {
    setEditing(false);
    setDraft("");
    onClose?.();
  };

  const mutate = (operation: FieldOp, v?: unknown, opts?: { keepOpen?: boolean }) => {
    edit.mutate(
      { cluster: cid, field, operation, value: v },
      {
        onSuccess: () => {
          pushToast("success", t("profile_view.field_saved"));
          if (opts?.keepOpen) {
            setDraft("");
            inputRef.current?.focus();
          } else {
            close();
          }
        },
      },
    );
  };

  const saveText = () => {
    const v = draft.trim();
    if (v) mutate("set", v);
    else if (!empty) mutate("clear");
    else close();
  };
  const addItem = (item: string) => {
    const v = item.trim();
    if (v) mutate("append", v, { keepOpen: true });
  };

  // ------------------------------------------------------------------ editing
  if (editing) {
    const choices = CHOICE_FIELDS[field];
    const items = kind === "list" && !empty ? (value as unknown[]).map(String) : [];
    const suggestions = (LIST_SUGGESTIONS[field] ?? []).filter((s) => !items.includes(s));
    const scaleHint = SCALE_FIELDS.has(field) ? t(`profile_view.scale_hint.${field}`) : null;

    let editor: React.ReactNode;
    if (choices || SCALE_FIELDS.has(field) || kind === "bool") {
      const options: { key: string; value: unknown; label: string }[] = choices
        ? choices.map((c) => ({ key: c, value: c, label: t(`profile_view.choices.${field}.${c}`) }))
        : kind === "bool"
          ? [
              { key: "yes", value: true, label: t("profile_view.value_yes") },
              { key: "no", value: false, label: t("profile_view.value_no") },
            ]
          : [1, 2, 3, 4, 5].map((n) => ({ key: String(n), value: n, label: String(n) }));
      const current = SCALE_FIELDS.has(field) ? scaleValue(value) : value;
      editor = (
        <div
          role="radiogroup"
          aria-label={label}
          className="flex w-full flex-wrap gap-1 rounded-lg bg-secondary p-1"
        >
          {options.map((o) => (
            <Segment
              key={o.key}
              active={current === o.value}
              disabled={busy}
              onClick={() => mutate("set", o.value)}
            >
              {o.label}
            </Segment>
          ))}
        </div>
      );
    } else if (kind === "list") {
      editor = (
        <div className="flex flex-col gap-3">
          {items.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {items.map((item) => (
                <span
                  key={item}
                  className="inline-flex items-center gap-1 rounded-md border border-border bg-secondary py-0.5 pl-2 pr-1 text-sm text-foreground"
                >
                  {choiceLabel(t, field, item)}
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => mutate("remove", item, { keepOpen: true })}
                    aria-label={`${t("profile_view.field_remove_item")}: ${item}`}
                    className="rounded-sm p-0.5 text-muted-foreground transition-colors hover:text-destructive focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    <X aria-hidden className="h-3 w-3" />
                  </button>
                </span>
              ))}
            </div>
          )}
          <div className="flex items-center gap-2">
            <Input
              ref={inputRef}
              value={draft}
              disabled={busy}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") addItem(draft);
                if (e.key === "Escape") close();
              }}
              placeholder={t("profile_view.field_add_placeholder")}
              aria-label={label}
              className="h-9"
            />
            <Button type="button" size="sm" variant="secondary" disabled={busy || !draft.trim()} onClick={() => addItem(draft)}>
              <Plus aria-hidden />
              {t("profile_view.field_add")}
            </Button>
          </div>
          {suggestions.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {suggestions.map((s) => (
                <button
                  key={s}
                  type="button"
                  disabled={busy}
                  onClick={() => addItem(s)}
                  className="inline-flex items-center gap-1 rounded-md border border-dashed border-border px-2 py-0.5 text-sm text-muted-foreground transition-colors hover:border-border-strong hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  <Plus aria-hidden className="h-3 w-3" />
                  {choiceLabel(t, field, s)}
                </button>
              ))}
            </div>
          )}
        </div>
      );
    } else {
      editor = (
        <Input
          ref={inputRef}
          value={draft}
          disabled={busy}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") saveText();
            if (e.key === "Escape") close();
          }}
          placeholder={t("profile_view.field_value_placeholder")}
          aria-label={label}
          className="h-9"
        />
      );
    }

    return (
      <div data-testid={`field-${field}`} className="bg-secondary/40 px-5 py-4">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <p className="text-base font-medium text-foreground-strong">{label}</p>
            {scaleHint && <p className="mt-0.5 text-sm text-muted-foreground">{scaleHint}</p>}
          </div>
          <span className="-my-1 flex shrink-0 items-center gap-1">
            {!empty && (
              <Button
                type="button"
                size="sm"
                variant="ghost"
                className="text-muted-foreground hover:text-destructive"
                disabled={busy}
                onClick={() => mutate("clear")}
              >
                {t("profile_view.field_clear")}
              </Button>
            )}
            <Button type="button" size="sm" variant="ghost" disabled={busy} onClick={close}>
              {typed ? t("profile_view.raw_cancel") : t("profile_view.field_done")}
            </Button>
          </span>
        </div>
        <div className="mt-3">
          {typed ? (
            <div className="flex items-center gap-2">
              {editor}
              <Button type="button" size="sm" disabled={busy} onClick={saveText}>
                <Check aria-hidden />
                {t("profile_view.raw_save")}
              </Button>
            </div>
          ) : (
            editor
          )}
        </div>
      </div>
    );
  }

  // ------------------------------------------------------------------ resting
  return (
    <div data-testid={`field-${field}`} className="group px-5 py-3.5 transition-colors hover:bg-secondary/40">
      <div className="flex items-center justify-between gap-6">
        <div className="min-w-0">
          <p className="text-base font-medium text-foreground">{label}</p>
          {latest && (
            <button
              type="button"
              onClick={() => setShowSource((v) => !v)}
              aria-expanded={showSource}
              data-testid={`entry-source-${field}`}
              title={t("profile_view.source_show")}
              className="mt-0.5 flex max-w-full items-center gap-1 rounded-sm text-left text-sm text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <span className="truncate">
                {t("profile_view.source_learned").replace("{0}", readableDate(latest.date))}
              </span>
            </button>
          )}
        </div>
        <div className="flex min-w-0 items-center gap-2">
          <ValueView field={field} value={value} />
          <Button
            type="button"
            variant="ghost"
            size="icon"
            onClick={open}
            title={t("profile_view.field_edit")}
            aria-label={`${t("profile_view.field_edit")}: ${label}`}
            className="h-8 w-8 shrink-0 text-muted-foreground opacity-0 transition-opacity focus-visible:opacity-100 group-hover:opacity-100"
          >
            <Pencil />
          </Button>
        </div>
      </div>

      {showSource && latest && (
        <div className="mt-3 rounded-lg border border-border bg-background/60 px-3.5 py-2.5">
          {latest.evidence ? (
            <p className="flex gap-2 text-sm text-foreground">
              <Quote aria-hidden className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground" />
              <span className="[overflow-wrap:anywhere]">{latest.evidence}</span>
            </p>
          ) : (
            <p className="text-sm text-muted-foreground">{t("profile_view.source_no_quote")}</p>
          )}
          {history.length > 1 && (
            <p className="mt-1.5 text-xs text-muted-foreground">
              {t("profile_view.source_revisions").replace("{0}", String(history.length))}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
