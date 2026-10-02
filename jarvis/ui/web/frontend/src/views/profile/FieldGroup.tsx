/**
 * One group of profile details — the known ones as rows, the missing ones as
 * a single line of "add" chips.
 *
 * A profile the assistant has barely started has sixteen empty fields. Listing
 * them as sixteen rows of "not set" makes the page a report on its own
 * emptiness; hiding them behind an "open fields" fold makes them impossible
 * to find. So every missing detail is one small chip at the foot of its group,
 * named, one click from its editor, and taking no more room than a sentence.
 */
import { useState } from "react";
import { Plus } from "lucide-react";

import { useT } from "@/i18n";
import { clusterDataOf } from "@/views/profile/api";
import { FieldRow } from "@/views/profile/FieldRow";
import { ProfileGroup } from "@/views/profile/ProfileGroup";
import { isEmptyValue, type FieldRef, type GroupId } from "@/views/profile/ledger";
import { historyFor, latestFor, type Observation } from "@/views/profile/provenance";

export function FieldGroup({
  id,
  fields,
  meta,
  observations,
}: {
  id: GroupId;
  fields: readonly FieldRef[];
  meta: Record<string, unknown>;
  observations: readonly Observation[];
}) {
  const t = useT();
  // The missing detail whose editor is open, if any. It renders as a row
  // until it is saved (then it is simply known) or closed.
  const [adding, setAdding] = useState<string | null>(null);

  const valueOf = (ref: FieldRef) => clusterDataOf(meta, ref.cid)[ref.field];
  const known = fields.filter((ref) => !isEmptyValue(valueOf(ref)));
  const missing = fields.filter((ref) => isEmptyValue(valueOf(ref)));
  const addingRef = missing.find((ref) => ref.field === adding) ?? null;
  const chips = missing.filter((ref) => ref.field !== adding);

  const row = (ref: FieldRef, initiallyEditing = false) => (
    <FieldRow
      key={ref.field}
      cid={ref.cid}
      field={ref.field}
      value={valueOf(ref)}
      latest={latestFor(observations, ref.cid, ref.field)}
      history={historyFor(observations, ref.cid, ref.field)}
      initiallyEditing={initiallyEditing}
      onClose={initiallyEditing ? () => setAdding(null) : undefined}
    />
  );

  return (
    <ProfileGroup
      testId={`group-${id}`}
      title={t(`profile_view.groups.${id}.title`)}
      description={t(`profile_view.groups.${id}.description`)}
      aside={
        <span className="font-mono text-sm tabular-nums text-muted-foreground">
          {known.length}/{fields.length}
        </span>
      }
    >
      {known.map((ref) => row(ref))}
      {addingRef && row(addingRef, true)}

      {chips.length > 0 && (
        <div data-testid={`missing-${id}`} className="px-5 py-4">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="mr-1 text-sm text-muted-foreground">{t("profile_view.add_label")}</span>
            {chips.map((ref) => (
              <button
                key={ref.field}
                type="button"
                onClick={() => setAdding(ref.field)}
                className="inline-flex items-center gap-1 rounded-full border border-border px-2.5 py-1 text-sm text-foreground transition-colors hover:border-border-strong hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <Plus aria-hidden className="h-3 w-3 text-muted-foreground" />
                {t(`profile_view.fields.${ref.field}`)}
              </button>
            ))}
          </div>
        </div>
      )}
    </ProfileGroup>
  );
}
