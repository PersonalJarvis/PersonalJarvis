/**
 * Computers — the servers and virtual machines {name} can work on besides
 * this one. A tab of the Settings hub (System group).
 *
 * One centred column. A small heading names the page; the card under it
 * holds what belongs to this PC and every machine at once (keeping IDE work
 * going when this PC closes, {name}'s own SSH key). Below, "Your computers"
 * lists each machine as one row (glyph, name, one subtitle line, status dot);
 * a row opens the machine's own page (Overview · Console · Access · Agents),
 * and "Add computer" opens the guided dialog.
 */
import { useMemo, useState } from "react";
import { Check, Copy, Loader2, Plus, RefreshCw } from "lucide-react";
import { Panel } from "@/components/extensions/primitives";
import { ComputersIcon } from "@/components/icons/sectionIcons";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Button } from "@/components/ui/button";
import { useCheckAll, useComputers, useIdentity } from "@/hooks/useComputers";
import { useLocaleChunk, useT } from "@/i18n";
import { robustCopy } from "@/lib/clipboard";
import type { Computer } from "@/lib/computersApi";
import { ComputerDetail, type DetailTab } from "@/views/computers/ComputerDetail";
import { ComputerRow } from "@/views/computers/ComputerRow";
import { KeepWorking } from "@/views/computers/KeepWorking";
import { needsAttention } from "@/views/computers/parts";
import { Empty, Group, Row, Section, sectionActionCls } from "@/views/computers/surface";
import { ConnectDialog } from "@/views/computers/ConnectDialog";

/** Keyframes the provisioning bar uses; scoped by name, shipped with the view. */
const KEYFRAMES = `@keyframes computers-indeterminate {
  0% { transform: translateX(-100%); }
  100% { transform: translateX(300%); }
}`;

function IdentityRow() {
  const t = useT();
  const identity = useIdentity();
  const [copied, setCopied] = useState(false);
  if (!identity.data) return null;
  const { algorithm, fingerprint, public_key } = identity.data;
  return (
    <Row
      title={t("computers.identity_title")}
      description={t("computers.identity_body")}
      status={
        <span className="block truncate font-mono" title={public_key}>
          {algorithm} · {fingerprint}
        </span>
      }
      control={
        <Button
          variant="outline"
          size="sm"
          aria-label={t("computers.copy")}
          onClick={() => {
            void robustCopy(public_key).then((ok) => {
              if (!ok) return;
              setCopied(true);
              window.setTimeout(() => setCopied(false), 1600);
            });
          }}
        >
          {copied ? <Check className="text-success" /> : <Copy />}
          {copied ? t("computers.copied") : t("computers.copy")}
        </Button>
      }
    />
  );
}

export function ComputersView() {
  const t = useT();
  useLocaleChunk("computers");
  const computers = useComputers();
  const checkAll = useCheckAll();
  const [open, setOpen] = useState<{ id: string; tab: DetailTab } | null>(null);
  const [adding, setAdding] = useState(false);

  const rows = useMemo(() => computers.data ?? [], [computers.data]);
  const current = open ? rows.find((c) => c.id === open.id) ?? null : null;
  const attention = rows.filter(needsAttention).length;
  const online = rows.filter((c) => c.health.status === "online").length;
  const summary = [
    t("computers.list_summary").replace("{online}", String(online)).replace("{total}", String(rows.length)),
    attention > 0 ? t("computers.list_attention").replace("{count}", String(attention)) : null,
  ]
    .filter(Boolean)
    .join(" · ");

  const openComputer = (computer: Computer, tab: DetailTab) => {
    setAdding(false);
    setOpen({ id: computer.id, tab });
  };

  return (
    <div className="flex h-full min-h-0 w-full flex-col" data-testid="computers-view">
      <style>{KEYFRAMES}</style>
      <ScrollArea className="min-h-0 flex-1">
        {current && open ? (
          <div className="flex w-full flex-col px-8 pb-10 pt-6">
            <ComputerDetail
              key={current.id}
              computer={current}
              initialTab={open.tab}
              onBack={() => setOpen(null)}
            />
          </div>
        ) : (
          <div className="mx-auto flex w-full max-w-4xl flex-col gap-8 px-6 pb-20 pt-10 sm:px-10">
            <div className="space-y-2.5">
              <header
                className="flex min-h-7 items-center justify-between gap-4 px-3 sm:px-4"
                data-testid="section-header"
              >
                <h1
                  className="flex min-w-0 items-center gap-2 text-base font-medium text-foreground-strong"
                  title={t("computers.subtitle")}
                >
                  <ComputersIcon className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
                  <span className="truncate">{t("computers.title")}</span>
                </h1>
                {rows.length > 0 && (
                  <span className="truncate text-sm text-muted-foreground">{summary}</span>
                )}
              </header>

              {computers.isLoading && (
                <div className="flex items-center gap-2 px-4 py-10 text-sm text-muted-foreground">
                  <Loader2 className="h-4 w-4 animate-spin" /> {t("computers.loading")}
                </div>
              )}
              {computers.isError && (
                <Panel className="p-5 text-sm text-destructive">{t("computers.load_failed")}</Panel>
              )}
              {computers.isSuccess && (
                <Group>
                  {rows.length > 0 && <KeepWorking computers={rows} />}
                  <IdentityRow />
                </Group>
              )}
            </div>

            {computers.isSuccess && (
              <Section
                title={t("computers.list_title")}
                action={
                  <>
                    {rows.length > 0 && (
                      <button
                        type="button"
                        className={sectionActionCls}
                        onClick={() => checkAll.mutate()}
                        disabled={checkAll.isPending}
                        data-testid="computers-check-all"
                      >
                        {checkAll.isPending ? <Loader2 className="animate-spin" /> : <RefreshCw />}
                        {t("computers.check_all")}
                      </button>
                    )}
                    <button
                      type="button"
                      className={sectionActionCls}
                      onClick={() => setAdding(true)}
                      data-testid="computers-add"
                    >
                      <Plus />
                      {t("computers.add")}
                    </button>
                  </>
                }
              >
                {rows.length === 0 ? (
                  <Empty
                    testId="computers-welcome"
                    icon={<ComputersIcon />}
                    title={t("computers.empty_title")}
                    description={t("computers.empty_body")}
                  >
                    <Button
                      variant="outline"
                      size="sm"
                      className="mt-5"
                      onClick={() => setAdding(true)}
                      data-testid="computers-add-first"
                    >
                      <Plus />
                      {t("computers.add")}
                    </Button>
                    <p className="mt-4 text-xs text-foreground-faint">{t("computers.empty_foot")}</p>
                  </Empty>
                ) : (
                  <ul className="divide-y divide-border" data-testid="computers-list">
                    {rows.map((computer) => (
                      <li key={computer.id}>
                        <ComputerRow
                          computer={computer}
                          checking={checkAll.isPending}
                          onOpen={() => setOpen({ id: computer.id, tab: "overview" })}
                        />
                      </li>
                    ))}
                  </ul>
                )}
              </Section>
            )}
          </div>
        )}
      </ScrollArea>

      {adding && <ConnectDialog onClose={() => setAdding(false)} onOpen={openComputer} />}
    </div>
  );
}
