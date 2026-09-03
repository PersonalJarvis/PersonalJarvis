/**
 * The knowledge ledger — one card per cluster, every field on the page.
 *
 * Nothing is hidden behind a "show more": a blank field is as informative as a
 * written one, so all eighteen are listed and the unknown ones say "not known
 * yet" in plain words. That sentence is deliberate — an earlier version drew a
 * redaction bar there, which read as information being withheld rather than
 * information not yet given.
 *
 * Every row is editable in place. The pencil is quiet until the row is hovered
 * or the button is focused, and the editor's shape follows the field kind:
 * text, a yes/no pair, or removable chips for a list.
 */
import { useEffect, useRef, useState } from "react";
import { Check, Pencil, Plus, Trash2, X } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { useT } from "@/i18n";
import { useEventStore } from "@/store/events";
import { clusterDataOf, renderValue, useFieldEdit, type FieldOp } from "@/views/profile/api";
import {
  CLUSTER_FIELD_KEYS,
  CLUSTER_ORDER,
  clusterFilledCount,
  fieldKind,
  isEmptyValue,
  type ClusterId,
} from "@/views/profile/ledger";

export function KnowledgeLedger({ meta }: { meta: Record<string, unknown> }) {
  return (
    <div className="grid gap-5 lg:grid-cols-2">
      {CLUSTER_ORDER.map((cid, i) => (
        <ClusterCard key={cid} cid={cid} meta={meta} delayMs={60 + i * 40} />
      ))}
    </div>
  );
}

function ClusterCard({
  cid,
  meta,
  delayMs,
}: {
  cid: ClusterId;
  meta: Record<string, unknown>;
  delayMs: number;
}) {
  const t = useT();
  const data = clusterDataOf(meta, cid);
  const fields = CLUSTER_FIELD_KEYS[cid];
  const filled = clusterFilledCount(meta, cid);
  const complete = filled === fields.length;
  const countLabel = t("profile_view.group_filled")
    .replace("{0}", String(filled))
    .replace("{1}", String(fields.length));

  return (
    <Card className="profile-rise flex flex-col" style={{ animationDelay: `${delayMs}ms` }}>
      <CardHeader className="flex-row items-center justify-between gap-3 pb-2">
        <CardTitle>{t(`profile_view.clusters.${cid}.label`)}</CardTitle>
        <Badge
          variant={complete ? "success" : "secondary"}
          aria-label={countLabel}
          title={countLabel}
          className="tabular-nums"
        >
          {filled}/{fields.length}
        </Badge>
      </CardHeader>
      <CardContent>
        <dl>
          {fields.map((key) => (
            <FieldRow key={key} cid={cid} fieldKey={key} value={data[key]} />
          ))}
        </dl>
      </CardContent>
    </Card>
  );
}

// ----------------------------------------------------------------------
// One field, read and written in place
// ----------------------------------------------------------------------

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

function FieldRow({
  cid,
  fieldKey,
  value,
}: {
  cid: ClusterId;
  fieldKey: string;
  value: unknown;
}) {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);
  const edit = useFieldEdit();
  const kind = fieldKind(fieldKey);
  const empty = isEmptyValue(value);
  const label = t(`profile_view.fields.${fieldKey}`);

  const [editing, setEditing] = useState(false);
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

  // ------------------------------------------------------------------ resting
  if (!editing) {
    return (
      <div className="group -mx-2 flex min-h-10 items-center justify-between gap-4 rounded-md px-2 py-1 transition-colors hover:bg-secondary">
        <dt className="shrink-0 text-base text-muted-foreground">{label}</dt>
        <dd className="flex min-w-0 items-center justify-end gap-2">
          {kind === "list" && !empty ? (
            <div className="flex min-w-0 flex-wrap justify-end gap-1">
              {(value as unknown[]).map((item) => (
                <Badge key={String(item)} variant="secondary" className="text-foreground">
                  {String(item)}
                </Badge>
              ))}
            </div>
          ) : empty ? (
            <span className="text-base text-foreground-faint">
              {t("profile_view.field_unknown")}
            </span>
          ) : (
            <span className="text-base text-foreground [overflow-wrap:anywhere]">
              {renderValue(t, value)}
            </span>
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
        </dd>
      </div>
    );
  }

  // ------------------------------------------------------------------ editing
  return (
    <div className="-mx-2 rounded-md bg-secondary px-2 py-2">
      <dt className="text-base font-medium text-foreground-strong">{label}</dt>
      <dd className="mt-2">
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
      </dd>
    </div>
  );
}
