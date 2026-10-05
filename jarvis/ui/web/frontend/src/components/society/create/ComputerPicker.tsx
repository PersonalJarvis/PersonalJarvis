/**
 * "Runs on" — where an agent's work executes: this computer, or one of the
 * machines connected in the Computers section (a VPS, a local VM). The value
 * is a computer id; "" means this computer. Shared by the create dialog and
 * the agent's spec sheet so the two can never offer different choices.
 */
import { Monitor, Server } from "lucide-react";
import { Combobox } from "@/components/ui/combobox";
import { useComputers } from "@/hooks/useComputers";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { useEventStore } from "@/store/events";
import type { Computer } from "@/lib/computersApi";

function Dot({ computer }: { computer: Computer }) {
  const status = computer.health.status;
  return (
    <span className="relative inline-flex h-4 w-4 items-center justify-center" aria-hidden>
      <Server className="h-3.5 w-3.5 text-muted-foreground" />
      <span
        className={cn(
          "absolute -bottom-0.5 -right-0.5 h-1.5 w-1.5 rounded-full",
          status === "online" ? "bg-success" : status === "unknown" ? "bg-foreground-faint" : "bg-warning",
        )}
      />
    </span>
  );
}

export function ComputerPicker({
  value,
  onChange,
  labelClass,
  testId = "society-computer-picker",
}: {
  value: string;
  onChange: (computerId: string) => void;
  labelClass?: string;
  testId?: string;
}) {
  const t = useT();
  const computers = useComputers();
  const setActiveSection = useEventStore((s) => s.setActiveSection);
  const rows = computers.data ?? [];
  const chosen = rows.find((c) => c.id === value);

  return (
    <div data-testid={testId}>
      <span className={labelClass}>{t("society.create.runs_on")}</span>
      <Combobox
        value={value}
        groups={[
          {
            id: "computers",
            options: [
              {
                value: "",
                label: t("society.create.runs_on_here"),
                icon: <Monitor className="h-3.5 w-3.5 text-muted-foreground" />,
              },
              ...rows.map((c) => ({
                value: c.id,
                label: c.name,
                hint: `${c.username}@${c.host}`,
                icon: <Dot computer={c} />,
              })),
            ],
          },
        ]}
        onChange={onChange}
        ariaLabel={t("society.create.runs_on")}
      />
      <p className="mt-1 text-xs text-muted-foreground">
        {chosen
          ? chosen.health.status === "online"
            ? t("society.create.runs_on_remote_hint")
            : t("society.create.runs_on_offline_hint")
          : t("society.create.runs_on_here_hint")}{" "}
        {rows.length === 0 && (
          <button
            type="button"
            onClick={() => setActiveSection("computers")}
            className="text-accent underline-offset-4 hover:underline"
          >
            {t("society.create.runs_on_connect")}
          </button>
        )}
      </p>
    </div>
  );
}
