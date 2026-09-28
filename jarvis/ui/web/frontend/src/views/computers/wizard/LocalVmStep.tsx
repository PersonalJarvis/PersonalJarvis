/**
 * Step 3 (local) — a virtual machine on this computer through Multipass:
 * a name and a size, created with {name}'s key already inside. Without
 * Multipass, the one thing to do is install it.
 */
import { useEffect, useMemo, useState } from "react";
import { ExternalLink, Loader2, MonitorSmartphone, RefreshCw, ShieldCheck } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useLocalStatus } from "@/hooks/useComputers";
import { useT } from "@/i18n";
import { openExternalUrl } from "@/lib/openExternal";
import { cn } from "@/lib/utils";
import { computersApi, type Computer } from "@/lib/computersApi";
import { Field, inputClass } from "../parts";
import { Choice, ErrorNote, errorText } from "./shared";
import { StepActions } from "./StepActions";

function nextFreeName(taken: string[]): string {
  if (!taken.includes("jarvis-vm")) return "jarvis-vm";
  for (let i = 2; i < 100; i += 1) {
    const candidate = `jarvis-vm-${i}`;
    if (!taken.includes(candidate)) return candidate;
  }
  return "jarvis-vm-x";
}

export function LocalVmStep({
  onBack,
  onAdded,
}: {
  onBack: () => void;
  onAdded: (computer: Computer) => void;
}) {
  const t = useT();
  const status = useLocalStatus();
  const taken = useMemo(() => (status.data?.instances ?? []).map((i) => i.name), [status.data]);
  const [name, setName] = useState("");
  const [image, setImage] = useState("24.04");
  const [cpus, setCpus] = useState(2);
  const [memory, setMemory] = useState(4);
  const [disk, setDisk] = useState(20);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!name && status.data) setName(nextFreeName(taken));
  }, [name, status.data, taken]);

  const nameValid = /^[a-z][a-z0-9-]{0,38}[a-z0-9]$/.test(name);

  async function create() {
    if (!nameValid || busy) return;
    setBusy(true);
    setError(null);
    try {
      const computer = await computersApi.createLocalVm({
        name,
        cpus,
        memory_gb: memory,
        disk_gb: disk,
        image,
      });
      onAdded(computer);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  if (status.isLoading) {
    return (
      <div className="flex items-center gap-2 py-8 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" /> {t("computers.local_checking")}
      </div>
    );
  }

  const data = status.data;
  if (!data || !data.available) {
    return (
      <div className="space-y-4 rounded-lg border border-border bg-card p-5">
        <div>
          <div className="text-title font-medium text-foreground-strong">{t("computers.local_missing_title")}</div>
          <p className="mt-1 text-sm text-muted-foreground">{t("computers.local_missing_body")}</p>
        </div>
        {data && (
          <code className="block rounded-md border border-border bg-surface-raised px-3 py-2 font-mono text-xs text-foreground-secondary">
            {data.install_hint}
          </code>
        )}
        <div className="flex flex-wrap gap-2">
          {data && (
            <Button type="button" onClick={() => void openExternalUrl(data.install_url)}>
              <ExternalLink />
              {t("computers.local_get_multipass")}
            </Button>
          )}
          <Button type="button" variant="outline" onClick={() => void status.refetch()}>
            <RefreshCw />
            {t("computers.check_again")}
          </Button>
        </div>
        <p className="text-xs text-muted-foreground">{t("computers.local_missing_foot")}</p>
        <StepActions onBack={onBack} />
      </div>
    );
  }

  return (
    <form
      className="space-y-5"
      onSubmit={(e) => {
        e.preventDefault();
        void create();
      }}
    >
      {data.error && <ErrorNote message={data.error} />}
      <Field
        label={t("computers.field_vm_name")}
        hint={nameValid || !name ? t("computers.vm_name_hint") : t("computers.vm_name_invalid")}
      >
        <input
          className={cn(inputClass, "font-mono")}
          value={name}
          onChange={(e) => setName(e.target.value.toLowerCase())}
          spellCheck={false}
          autoFocus
        />
      </Field>
      <div className="grid gap-5 sm:grid-cols-2">
        <div>
          <div className="mb-1.5 text-sm font-medium text-foreground-secondary">{t("computers.field_image")}</div>
          <Choice
            label={t("computers.field_image")}
            value={image}
            onChange={setImage}
            options={data.images.map((i) => ({ id: i, label: `Ubuntu ${i}` }))}
          />
        </div>
        <div>
          <div className="mb-1.5 text-sm font-medium text-foreground-secondary">{t("computers.field_cpus")}</div>
          <Choice
            label={t("computers.field_cpus")}
            value={cpus}
            onChange={setCpus}
            options={[1, 2, 4, 8].map((n) => ({ id: n, label: String(n) }))}
          />
        </div>
        <div>
          <div className="mb-1.5 text-sm font-medium text-foreground-secondary">{t("computers.field_memory")}</div>
          <Choice
            label={t("computers.field_memory")}
            value={memory}
            onChange={setMemory}
            options={[2, 4, 8, 16].map((n) => ({ id: n, label: `${n} GB` }))}
          />
        </div>
        <div>
          <div className="mb-1.5 text-sm font-medium text-foreground-secondary">{t("computers.field_disk")}</div>
          <Choice
            label={t("computers.field_disk")}
            value={disk}
            onChange={setDisk}
            options={[10, 20, 40, 80].map((n) => ({ id: n, label: `${n} GB` }))}
          />
        </div>
      </div>
      <div className="flex items-start gap-3 rounded-lg border border-border bg-card p-4 text-sm">
        <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-success" aria-hidden />
        <p className="text-muted-foreground">{t("computers.local_key_note")}</p>
      </div>
      <ErrorNote message={error} />
      <p className="text-xs text-muted-foreground">Multipass {data.version ?? ""}</p>
      <StepActions onBack={onBack}>
        <Button type="submit" disabled={!nameValid || busy}>
          {busy ? <Loader2 className="animate-spin" /> : <MonitorSmartphone />}
          {t("computers.create_vm")}
        </Button>
      </StepActions>
    </form>
  );
}

