/**
 * Computers — the machines Jarvis can work on besides this one.
 *
 * A rented VPS (any SSH host, or imported from Hostinger / Hetzner /
 * DigitalOcean) and local virtual machines created through Multipass, in one
 * list. The list is the landing: a row of headline numbers, then one "rack
 * unit" per machine with its live meters; a row opens the detail page in
 * place. An empty section opens straight onto the three ways in, so the first
 * visit is a choice, not a blank table.
 *
 * Every machine is reached the same way — over SSH with Jarvis's own key —
 * which is what later features (deploying Jarvis to a server, agents working
 * on a remote box) build on.
 */
import { useMemo, useState } from "react";
import {
  Fingerprint,
  KeyRound,
  Loader2,
  Plus,
  RefreshCw,
  Server,
  ShieldCheck,
  Sparkles,
} from "lucide-react";
import { Panel, StatGroup, StatTile } from "@/components/extensions/primitives";
import { PageHeader } from "@/components/layout/PageHeader";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Button } from "@/components/ui/button";
import { useCheckAll, useComputers, useIdentity } from "@/hooks/useComputers";
import { useLocaleChunk, useT } from "@/i18n";
import type { Computer } from "@/lib/computersApi";
import { AddComputerDialog, PathChoices, type AddPath } from "@/views/computers/AddComputerDialog";
import { ComputerDetail } from "@/views/computers/ComputerDetail";
import { ComputerRow } from "@/views/computers/ComputerRow";
import { CopyField, needsAttention } from "@/views/computers/parts";
import { KeepWorking } from "@/views/computers/KeepWorking";

/** Keyframes the provisioning bar uses; scoped by name, shipped with the view. */
const KEYFRAMES = `@keyframes computers-indeterminate {
  0% { transform: translateX(-100%); }
  100% { transform: translateX(300%); }
}`;

function Welcome({ onPick }: { onPick: (path: AddPath) => void }) {
  const t = useT();
  const steps = [
    { icon: <Plus />, title: t("computers.how_1_title"), body: t("computers.how_1_body") },
    { icon: <KeyRound />, title: t("computers.how_2_title"), body: t("computers.how_2_body") },
    { icon: <ShieldCheck />, title: t("computers.how_3_title"), body: t("computers.how_3_body") },
  ];
  return (
    <div className="flex flex-col gap-8" data-testid="computers-welcome">
      <div className="relative overflow-hidden rounded-xl border border-border bg-card px-8 py-10">
        {/* A quiet rack of status lights: decoration drawn from the section's own vocabulary. */}
        <div aria-hidden className="pointer-events-none absolute right-8 top-1/2 hidden -translate-y-1/2 gap-2 lg:flex">
          {[0, 1, 2].map((col) => (
            <div key={col} className="flex flex-col gap-2">
              {[0, 1, 2, 3].map((row) => (
                <div
                  key={row}
                  className="flex h-7 w-28 items-center gap-2 rounded-md border border-border bg-background/60 px-2.5"
                >
                  <span
                    className="h-1.5 w-1.5 rounded-full bg-success motion-safe:animate-pulse"
                    style={{ animationDelay: `${(col * 4 + row) * 180}ms`, opacity: (row + col) % 3 === 0 ? 1 : 0.45 }}
                  />
                  <span className="h-1 flex-1 rounded-full bg-secondary" />
                </div>
              ))}
            </div>
          ))}
        </div>
        <div className="relative max-w-xl">
          <span className="inline-flex items-center gap-1.5 rounded-full border border-border px-2.5 py-1 text-xs font-medium text-muted-foreground">
            <Sparkles className="h-3 w-3" aria-hidden />
            {t("computers.welcome_badge")}
          </span>
          <h2 className="mt-4 text-2xl font-semibold text-foreground-strong">{t("computers.welcome_title")}</h2>
          <p className="mt-2 text-base text-muted-foreground">{t("computers.welcome_body")}</p>
        </div>
      </div>

      <PathChoices onPick={onPick} />

      <ol className="grid gap-4 md:grid-cols-3">
        {steps.map((step, index) => (
          <li key={step.title} className="flex gap-3">
            <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-border text-xs tabular-nums text-muted-foreground">
              {index + 1}
            </span>
            <span>
              <span className="block text-base font-medium text-foreground-strong">{step.title}</span>
              <span className="mt-0.5 block text-sm text-muted-foreground">{step.body}</span>
            </span>
          </li>
        ))}
      </ol>
    </div>
  );
}

function IdentityStrip() {
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
  const [openId, setOpenId] = useState<string | null>(null);
  const [adding, setAdding] = useState<{ path: AddPath | null } | null>(null);

  const rows = useMemo(() => computers.data ?? [], [computers.data]);
  const open = rows.find((c) => c.id === openId) ?? null;
  const stats = useMemo(() => {
    const online = rows.filter((c) => c.health.status === "online");
    const latencies = online.map((c) => c.health.latency_ms).filter((v): v is number => v !== null);
    return {
      total: rows.length,
      online: online.length,
      attention: rows.filter(needsAttention).length,
      vms: rows.filter((c) => c.kind === "local_vm").length,
      latency: latencies.length ? Math.round(latencies.reduce((a, b) => a + b, 0) / latencies.length) : null,
    };
  }, [rows]);

  const onAdded = (computer: Computer) => {
    setAdding(null);
    setOpenId(computer.id);
  };

  return (
    <div className="flex h-full min-h-0 w-full flex-col" data-testid="computers-view">
      <style>{KEYFRAMES}</style>
      <div className="w-full shrink-0 px-8">
        <PageHeader
          icon={<Server />}
          title={t("computers.title")}
          description={t("computers.subtitle")}
          actions={
            rows.length > 0 && !open ? (
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
                <Button onClick={() => setAdding({ path: null })} data-testid="computers-add">
                  <Plus />
                  {t("computers.add")}
                </Button>
              </>
            ) : null
          }
        />
      </div>

      <ScrollArea className="min-h-0 flex-1">
        <div className="flex w-full flex-col gap-5 px-8 pb-10">
          {computers.isLoading && (
            <div className="flex items-center gap-2 py-10 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" /> {t("computers.loading")}
            </div>
          )}
          {computers.isError && (
            <Panel className="p-5 text-sm text-destructive">{t("computers.load_failed")}</Panel>
          )}

          {open && <ComputerDetail key={open.id} computer={open} onBack={() => setOpenId(null)} />}

          {!open && computers.isSuccess && rows.length === 0 && (
            <Welcome onPick={(path) => setAdding({ path })} />
          )}

          {!open && rows.length > 0 && (
            <>
              <StatGroup>
                <StatTile cell icon={<Server className="h-4 w-4" />} label={t("computers.stat_total")} value={stats.total} hint={stats.vms ? `${stats.vms} ${t("computers.stat_vms")}` : t("computers.stat_no_vms")} />
                <StatTile cell tone="success" icon={<span className="block h-2 w-2 rounded-full bg-success" />} label={t("computers.stat_online")} value={`${stats.online}/${stats.total}`} hint={t("computers.stat_online_hint")} />
                <StatTile cell tone={stats.attention ? "warn" : "ok"} icon={<ShieldCheck className="h-4 w-4" />} label={t("computers.stat_attention")} value={stats.attention} hint={stats.attention ? t("computers.stat_attention_hint") : t("computers.stat_all_good")} />
                <StatTile cell icon={<RefreshCw className="h-4 w-4" />} label={t("computers.stat_latency")} value={stats.latency === null ? "—" : `${stats.latency} ms`} hint={t("computers.stat_latency_hint")} />
              </StatGroup>

              <Panel className="overflow-hidden p-0">
                <ul className="divide-y divide-border" data-testid="computers-list">
                  {rows.map((computer) => (
                    <ComputerRow
                      key={computer.id}
                      computer={computer}
                      checking={checkAll.isPending}
                      onOpen={() => setOpenId(computer.id)}
                    />
                  ))}
                </ul>
              </Panel>

              <KeepWorking computers={rows} />

              <IdentityStrip />
            </>
          )}
        </div>
      </ScrollArea>

      {adding && (
        <AddComputerDialog
          initialPath={adding.path}
          onClose={() => setAdding(null)}
          onAdded={onAdded}
        />
      )}
    </div>
  );
}
