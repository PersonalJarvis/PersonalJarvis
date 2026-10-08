/**
 * Computers — the servers and virtual machines {name} can work on besides
 * this one. A tab of the Settings hub (System group).
 *
 * The page opens on an overview of the fleet (how many are up, their cores,
 * memory and latency), then one card per machine with its state and live
 * load; a card opens the machine's own page (Overview · Console · Access · Agents).
 * Adding one is a guided four-step wizard. Below the table sit the two
 * settings that belong to all machines at once: keeping IDE work going when
 * this PC closes, and {name}'s own SSH key.
 */
import { useMemo, useState } from "react";
import { Fingerprint, Loader2, Plus, RefreshCw } from "lucide-react";
import { Panel } from "@/components/extensions/primitives";
import { ComputersIcon } from "@/components/icons/sectionIcons";
import { PageHeader } from "@/components/layout/PageHeader";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Button } from "@/components/ui/button";
import { useCheckAll, useComputers, useIdentity } from "@/hooks/useComputers";
import { useLocaleChunk, useT } from "@/i18n";
import type { Computer } from "@/lib/computersApi";
import { ComputerDetail, type DetailTab } from "@/views/computers/ComputerDetail";
import { ComputerRow } from "@/views/computers/ComputerRow";
import { KeepWorking } from "@/views/computers/KeepWorking";
import { CopyField, SettingIcon, StatusLight, formatMemory, needsAttention } from "@/views/computers/parts";
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
      <span className="flex h-12 w-12 items-center justify-center rounded-xl bg-accent-soft text-accent ring-1 ring-inset ring-accent/20">
        <ComputersIcon className="h-6 w-6" aria-hidden />
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

/** The fleet at a glance: how many machines are up, and what they add up to. */
function Overview({ rows }: { rows: Computer[] }) {
  const t = useT();
  const total = rows.length;
  const online = rows.filter((c) => c.health.status === "online");
  const attention = rows.filter(needsAttention).length;
  const cores = rows.reduce((sum, c) => sum + (c.facts?.cpu_count ?? 0), 0);
  const memoryMb = rows.reduce((sum, c) => sum + (c.facts?.mem_total_mb ?? 0), 0);
  const latencies = online.map((c) => c.health.latency_ms).filter((ms): ms is number => ms !== null);
  const latency = latencies.length ? Math.round(latencies.reduce((a, b) => a + b, 0) / latencies.length) : null;

  const status =
    attention > 0
      ? { tone: "warn" as const, text: t("computers.list_attention").replace("{count}", String(attention)) }
      : online.length === total
        ? { tone: "ok" as const, text: t("computers.overview_all_online") }
        : {
            tone: "off" as const,
            text: t("computers.list_summary")
              .replace("{online}", String(online.length))
              .replace("{total}", String(total)),
          };
  const figures = [
    { label: t("computers.overview_cores"), value: cores ? String(cores) : "—" },
    { label: t("computers.overview_memory"), value: formatMemory(memoryMb) ?? "—" },
    { label: t("computers.overview_latency"), value: latency === null ? "—" : `${latency} ms` },
  ];

  return (
    <div
      className="relative overflow-hidden rounded-xl border border-border bg-card"
      data-testid="computers-overview"
    >
      <div
        aria-hidden
        className="pointer-events-none absolute -left-20 -top-28 h-72 w-72 rounded-full bg-accent-soft blur-3xl"
      />
      <div className="relative flex flex-col gap-5 p-5 md:flex-row md:items-center md:gap-8">
        <div className="flex min-w-0 items-center gap-4 md:flex-1">
          <span className="flex h-14 w-14 shrink-0 items-center justify-center rounded-2xl bg-accent-soft text-accent ring-1 ring-inset ring-accent/25">
            <ComputersIcon className="h-7 w-7" strokeWidth={1.7} aria-hidden />
          </span>
          <div className="min-w-0">
            <div className="text-2xl font-semibold tabular-nums tracking-tight text-foreground-strong">
              {online.length}
              <span className="text-foreground-faint"> / {total}</span>
            </div>
            <div className="mt-0.5 flex items-center gap-2 text-sm text-muted-foreground">
              <StatusLight tone={status.tone} />
              <span className="truncate">{status.text}</span>
            </div>
          </div>
        </div>
        <dl className="grid grid-cols-3 gap-px overflow-hidden rounded-lg border border-border bg-border md:w-[26rem]">
          {figures.map((figure) => (
            <div key={figure.label} className="min-w-0 bg-card px-4 py-3">
              <dt className="truncate text-xs text-muted-foreground">{figure.label}</dt>
              <dd className="mt-1 truncate text-lg font-semibold tabular-nums text-foreground-strong">{figure.value}</dd>
            </div>
          ))}
        </dl>
      </div>
    </div>
  );
}

function IdentityRow() {
  const t = useT();
  const identity = useIdentity();
  if (!identity.data) return null;
  return (
    <Panel className="rounded-xl p-5">
      <div className="flex items-start gap-4">
        <SettingIcon>
          <Fingerprint />
        </SettingIcon>
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
              icon={<ComputersIcon />}
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
                <Overview rows={rows} />
                <h2 id="computers-list-title" className="mb-3 mt-8 text-base font-semibold text-foreground-strong">
                  {t("computers.list_title")}
                </h2>
                <ul className="space-y-2.5" data-testid="computers-list">
                  {rows.map((computer) => (
                    <ComputerRow
                      key={computer.id}
                      computer={computer}
                      checking={checkAll.isPending}
                      onOpen={() => setOpen({ id: computer.id, tab: "overview" })}
                    />
                  ))}
                </ul>
              </section>

              <section aria-labelledby="computers-settings-title" className="mt-2 space-y-3">
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
