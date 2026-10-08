/**
 * Computers — the servers and virtual machines {name} can work on besides
 * this one. A tab of the Settings hub (System group).
 *
 * The page reads like the other settings pages: one centred column, a short
 * heading above each grouped card, one hairline-split row per item. The first
 * card lists the machines with their state and load; a row opens the
 * machine's own page (Overview · Console · Access · Agents). Adding one is a
 * guided dialog. The second card holds the two settings that belong to all
 * machines at once: keeping IDE work going when this PC closes, and {name}'s
 * own SSH key.
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
import { ConnectDialog } from "@/views/computers/ConnectDialog";
import { SettingsCard, SettingsRow, SettingsSection } from "@/views/settings/SettingsLayout";

/** Keyframes the provisioning bar uses; scoped by name, shipped with the view. */
const KEYFRAMES = `@keyframes computers-indeterminate {
  0% { transform: translateX(-100%); }
  100% { transform: translateX(300%); }
}`;

/** A quiet text action beside a section heading. */
const sectionActionCls = "h-8 px-2.5 text-sm font-medium text-muted-foreground hover:text-foreground";

function EmptyState({ onAdd }: { onAdd: () => void }) {
  const t = useT();
  return (
    <div className="flex flex-col items-center px-6 py-14 text-center" data-testid="computers-welcome">
      <span className="flex h-10 w-10 items-center justify-center rounded-lg border border-border bg-secondary text-foreground-secondary">
        <ComputersIcon className="h-5 w-5" aria-hidden />
      </span>
      <h2 className="mt-5 text-lg font-semibold text-foreground-strong">{t("computers.empty_title")}</h2>
      <p className="mt-1.5 max-w-md text-sm text-muted-foreground">{t("computers.empty_body")}</p>
      <Button variant="outline" size="sm" className="mt-5" onClick={onAdd} data-testid="computers-add-first">
        <Plus />
        {t("computers.add")}
      </Button>
      <p className="mt-4 text-xs text-foreground-faint">{t("computers.empty_foot")}</p>
    </div>
  );
}

function IdentityRow() {
  const t = useT();
  const identity = useIdentity();
  const [copied, setCopied] = useState(false);
  if (!identity.data) return null;
  const { algorithm, fingerprint, public_key } = identity.data;
  return (
    <SettingsRow
      title={t("computers.identity_title")}
      description={
        <>
          {t("computers.identity_body")}
          <span className="mt-1 block truncate font-mono text-xs text-foreground-faint" title={public_key}>
            {algorithm} · {fingerprint}
          </span>
        </>
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
          <div className="mx-auto w-full max-w-4xl px-6 pb-20 pt-10 sm:px-10">
            <header className="mb-8" data-testid="section-header">
              <h1 className="flex items-center gap-2.5 font-display text-2xl font-semibold text-foreground-strong">
                <ComputersIcon className="h-6 w-6 text-muted-foreground" aria-hidden />
                {t("computers.title")}
              </h1>
              <p className="mt-1.5 text-base text-muted-foreground">{t("computers.subtitle")}</p>
            </header>

            {computers.isLoading && (
              <div className="flex items-center gap-2 py-10 text-sm text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" /> {t("computers.loading")}
              </div>
            )}
            {computers.isError && (
              <Panel className="p-5 text-sm text-destructive">{t("computers.load_failed")}</Panel>
            )}

            {computers.isSuccess && (
              <div className="space-y-10">
                <SettingsSection
                  title={t("computers.list_title")}
                  description={rows.length > 0 ? summary : undefined}
                  actions={
                    rows.length > 0 ? (
                      <>
                        <Button
                          variant="ghost"
                          className={sectionActionCls}
                          onClick={() => checkAll.mutate()}
                          disabled={checkAll.isPending}
                          data-testid="computers-check-all"
                        >
                          {checkAll.isPending ? <Loader2 className="animate-spin" /> : <RefreshCw />}
                          {t("computers.check_all")}
                        </Button>
                        <Button
                          variant="ghost"
                          className={sectionActionCls}
                          onClick={() => setAdding(true)}
                          data-testid="computers-add"
                        >
                          <Plus />
                          {t("computers.add")}
                        </Button>
                      </>
                    ) : null
                  }
                >
                  <SettingsCard className="overflow-hidden">
                    {rows.length === 0 ? (
                      <EmptyState onAdd={() => setAdding(true)} />
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
                  </SettingsCard>
                </SettingsSection>

                {rows.length > 0 && (
                  <SettingsSection title={t("computers.settings_title")}>
                    <SettingsCard>
                      <KeepWorking computers={rows} />
                      <IdentityRow />
                    </SettingsCard>
                  </SettingsSection>
                )}
              </div>
            )}
          </div>
        )}
      </ScrollArea>

      {adding && <ConnectDialog onClose={() => setAdding(false)} onOpen={openComputer} />}
    </div>
  );
}
