/**
 * Recent changes: every note the assistant added, changed or removed, newest
 * first, read from the learning ledger. Each line names the file, what
 * happened and when, and quotes what the person said when there is a quote —
 * so "why does it think that?" has an answer on the page.
 */
import { Minus, PencilLine, Plus } from "lucide-react";
import type { ReactNode } from "react";

import { useT, useUiLanguage } from "@/i18n";
import { cn } from "@/lib/utils";
import { ProfileGroup } from "@/views/profile/ProfileGroup";
import type { SoulActivity } from "@/views/soul/api";
import { parseLedgerTime, whenShort } from "@/views/soul/format";
import { splitDated } from "@/views/soul/NotesGroup";

const FILE_OF_TARGET: Record<string, string> = {
  soul: "SOUL.md",
  memory: "MEMORY.md",
  user: "USER.md",
};

const OP_ICON: Record<string, ReactNode> = {
  add: <Plus />,
  replace: <PencilLine />,
  remove: <Minus />,
};

export function ActivityGroup({ activity }: { activity: readonly SoulActivity[] }) {
  const t = useT();
  const lang = useUiLanguage();

  return (
    <ProfileGroup
      testId="soul-activity"
      title={t("soul_view.activity_title")}
      description={t("soul_view.activity_description")}
    >
      {activity.length === 0 ? (
        <p className="px-5 py-5 text-base text-muted-foreground">{t("soul_view.activity_empty")}</p>
      ) : (
        <ol className="px-5 py-2">
          {activity.map((item, i) => {
            const op = OP_ICON[item.operation] ? item.operation : "add";
            const when = whenShort(parseLedgerTime(item.ts), lang);
            const last = i === activity.length - 1;
            return (
              <li key={`${item.ts}-${i}`} className="relative flex gap-4 py-3">
                {!last && (
                  <span aria-hidden className="absolute left-3 top-10 bottom-0 w-px bg-border" />
                )}
                <span
                  aria-hidden
                  className={cn(
                    "relative flex h-6 w-6 shrink-0 items-center justify-center rounded-full border [&>svg]:h-3.5 [&>svg]:w-3.5",
                    op === "remove"
                      ? "border-border bg-secondary text-muted-foreground"
                      : "border-accent/20 bg-accent-soft text-accent",
                  )}
                >
                  {OP_ICON[op]}
                </span>
                <div className="min-w-0 flex-1">
                  <p
                    className={cn(
                      "text-base leading-6",
                      op === "remove" ? "text-muted-foreground line-through decoration-foreground-faint" : "text-foreground",
                    )}
                  >
                    {splitDated(item.text).body || "–"}
                  </p>
                  <p className="mt-0.5 text-sm text-muted-foreground">
                    <span className="font-mono">{FILE_OF_TARGET[item.target] ?? item.target}</span>
                    {" · "}
                    {t(`soul_view.op_${op}`)}
                    {when && ` · ${when}`}
                  </p>
                  {item.evidence && item.evidence !== "[withheld]" && (
                    <p className="mt-1.5 border-l-2 border-border pl-3 text-sm italic text-muted-foreground">
                      “{item.evidence}”
                    </p>
                  )}
                </div>
              </li>
            );
          })}
        </ol>
      )}
    </ProfileGroup>
  );
}
