/**
 * Computers — the servers and virtual machines {name} can work on besides
 * this one. A tab of the Settings hub (System group).
 *
 * The page is a quiet table of machines with their state and live load; a
 * row opens the machine's own page (Overview · Console · Access · Agents).
 * Adding one is a guided four-step wizard. Below the table sit the two
 * settings that belong to all machines at once: keeping IDE work going when
 * this PC closes, and {name}'s own SSH key.
 */
import { useMemo, useState } from "react";
import { Fingerprint, Loader2, Plus, RefreshCw, Server } from "lucide-react";
import { Panel } from "@/components/extensions/primitives";
import { PageHeader } from "@/components/layout/PageHeader";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Button } from "@/components/ui/button";
import { useCheckAll, useComputers, useIdentity } from "@/hooks/useComputers";
import { useLocaleChunk, useT } from "@/i18n";
import type { Computer } from "@/lib/computersApi";
import { ComputerDetail, type DetailTab } from "@/views/computers/ComputerDetail";
import { ComputerRow, ComputerTableHead } from "@/views/computers/ComputerRow";
import { KeepWorking } from "@/views/computers/KeepWorking";
import { CopyField, needsAttention } from "@/views/computers/parts";
import { ConnectDialog } from "@/views/computers/ConnectDialog";

/** Keyframes the provisioning bar uses; scoped by name, shipped with the view. */
const KEYFRAMES = `@keyframes computers-indeterminate {
  0% { transform: translateX(-100%); }
  100% { transform: translateX(300%); }
}`;

function EmptyState({ onAdd }: { onAdd: () => void }) {
  const t = useT();
  return (
    <div
      className="flex flex-col items-center rounded-xl border border-dashed border-border px-6 py-16 text-center"
      data-testid="computers-welcome"
    >
      <span className="flex h-12 w-12 items-center justify-center rounded-full bg-secondary text-muted-foreground">
        <Server className="h-5 w-5" aria-hidden />
      </span>
      <h2 className="mt-4 text-lg font-semibold text-foreground-strong">{t("computers.empty_title")}</h2>
      <p className="mt-1.5 max-w-md text-base text-muted-foreground">{t("computers.empty_body")}</p>
      <Button className="mt-6" onClick={onAdd} data-testid="computers-add-first">
        <Plus />
        {t("computers.add")}
      </Button>
      <p className="mt-4 text-xs text-muted-foreground">{t("computers.empty_foot")}</p>
    </div>
  );
}

function IdentityRow() {
  const t = useT();
  const identity = useIdentity();
  if (!identity.data) return null;
  return (
    <Panel className="p-5">
      <div className="flex items-start gap-3">
        <Fingerprint className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
        <div className="min-w-0 flex-1">
          <div className="text-base font-semibold text-foreground-strong">{t("computers.identity_title")}</div>
          <p className="mt-0.5 text-sm text-muted-foreground">{t("computers.identity_body")}</p>
          <div className="mt-4">
            <CopyField
              label={`${identity.data.algorithm} · ${identity.data.fingerprint}`}
              value={identity.data.public_key}
              copyLabel={t("computers.copy")}
              copiedLabel={t("computers.copied")}
            />
          </div>
        </div>
      </div>
    </Panel>
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

  const openComputer = (computer: Computer, tab: DetailTab) => {
    setAdding(false);
    setOpen({ id: computer.id, tab });
  };

  return (
    <div className="flex h-full min-h-0 w-full flex-col" data-testid="computers-view">
      <style>{KEYFRAMES}</style>
      <ScrollArea className="min-h-0 flex-1">
        <div className="flex w-full flex-col gap-6 px-8 pb-10">
          {!current && (
            <PageHeader
              icon={<Server />}
              title={t("computers.title")}
              description={t("computers.subtitle")}
              actions={
                rows.length > 0 ? (
                  <>
                    <Button
                      variant="outline"
                      onClick={() => checkAll.mutate()}
                      disabled={checkAll.isPending}
                      data-testid="computers-check-all"
                    >
                      {checkAll.isPending ? <Loader2 className="animate-spin" /> : <RefreshCw />}
                      {t("computers.check_all")}
                    </Button>
                    <Button onClick={() => setAdding(true)} data-testid="computers-add">
                      <Plus />
                      {t("computers.add")}
                    </Button>
                  </>
                ) : null
              }
            />
          )}

          {computers.isLoading && (
            <div className="flex items-center gap-2 py-10 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" /> {t("computers.loading")}
            </div>
          )}
          {computers.isError && (
            <Panel className="p-5 text-sm text-destructive">{t("computers.load_failed")}</Panel>
          )}

          {current && open && (
            <div className="pt-6">
              <ComputerDetail
                key={current.id}
                computer={current}
                initialTab={open.tab}
                onBack={() => setOpen(null)}
              />
            </div>
          )}

          {!current && computers.isSuccess && rows.length === 0 && <EmptyState onAdd={() => setAdding(true)} />}

          {!current && rows.length > 0 && (
            <>
              <section aria-labelledby="computers-list-title">
                <div className="mb-3 flex items-baseline justify-between gap-3">
                  <h2 id="computers-list-title" className="text-base font-semibold text-foreground-strong">
                    {t("computers.list_title")}
                  </h2>
                  <span className="text-sm text-muted-foreground">
                    {t("computers.list_summary")
                      .replace("{online}", String(online))
                      .replace("{total}", String(rows.length))}
                    {attention > 0 && ` · ${t("computers.list_attention").replace("{count}", String(attention))}`}
                  </span>
                </div>
                <div className="overflow-hidden rounded-lg border border-border bg-card" role="table">
                  <ComputerTableHead />
                  <ul className="divide-y divide-border" data-testid="computers-list" role="rowgroup">
                    {rows.map((computer) => (
                      <ComputerRow
                        key={computer.id}
                        computer={computer}
                        checking={checkAll.isPending}
                        onOpen={() => setOpen({ id: computer.id, tab: "overview" })}
                      />
                    ))}
                  </ul>
                </div>
              </section>

              <section aria-labelledby="computers-settings-title" className="space-y-4">
                <h2 id="computers-settings-title" className="text-base font-semibold text-foreground-strong">
                  {t("computers.settings_title")}
                </h2>
                <KeepWorking computers={rows} />
                <IdentityRow />
              </section>
            </>
          )}
        </div>
      </ScrollArea>

      {adding && <ConnectDialog onClose={() => setAdding(false)} onOpen={openComputer} />}
    </div>
  );
}
