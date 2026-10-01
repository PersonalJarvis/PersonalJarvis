/** The model seat one routine runs on — pinned at creation, changeable here. */
import { useT } from "@/i18n";
import { useAgentChat } from "@/components/agentchat/AgentChatStoreContext";
import { BrandedSelect } from "@/components/ui/select";
import { effortsFor } from "../create/brainPicker";
import { modelSeats, providerTitle } from "../chat/modelChoices";
import { useModelMenuData } from "../chat/useModelMenuData";

export interface RoutineSeat {
  provider: string;
  model: string;
  effort: string;
  account_id: string;
}

export const EMPTY_SEAT: RoutineSeat = { provider: "", model: "", effort: "", account_id: "" };

const field = "w-full rounded-lg border border-border bg-background px-3 py-2 text-[12px] text-foreground";
const miniLabel = "block space-y-1 text-[11px] text-muted-foreground";

function kindSuffix(kind: string, t: (key: string) => string): string {
  try {
    return t(`society.create.kind_${kind}`);
  } catch {
    return kind;
  }
}

export function RoutineSeatPicker({ seat, onChange, disabled }: {
  seat: RoutineSeat;
  onChange: (next: RoutineSeat) => void;
  disabled: boolean;
}) {
  const t = useT();
  const label = (key: string) => t(`society.routine_detail.${key}`);
  const chatCatalog = useAgentChat((state) => (state.surface === "society" ? state.catalog : null));
  const chatConnections = useAgentChat((state) => state.connections);
  const { options, providers, live, loading } = useModelMenuData(chatCatalog, chatConnections, { [seat.provider]: seat.account_id });
  // The catalog query can resolve to anything while the backend is away; only
  // arrays reach the seat join so the picker degrades to free text, never a crash.
  const rows = Array.isArray(providers) ? providers : [];
  const seats = modelSeats(options, rows, live, t("agent_chat.model_default"));
  const active = seats.find((s) => s.provider.id === seat.provider) ?? null;
  const models = active?.provider.curated_models ?? [];
  const efforts = active ? effortsFor(active, seat.model) : [];
  const showAllModels = models.length === 0;

  return (
    <div className="space-y-2 rounded-xl border border-border p-3">
      <div className="flex items-center justify-between gap-2">
        <h4 className="text-[11px] text-muted-foreground">{label("model")}</h4>
        {seat.provider ? (
          <button
            type="button"
            className="text-[11px] text-muted-foreground underline disabled:opacity-50"
            disabled={disabled}
            onClick={() => onChange({ ...EMPTY_SEAT })}
          >
            {label("follow_agent")}
          </button>
        ) : null}
      </div>
      {!seat.provider ? (
        <p className="text-[12px] text-muted-foreground">{label("model_follows_agent")}</p>
      ) : null}
      <div className={miniLabel}>
        <span>{label("provider")}</span>
        <BrandedSelect
          className={field}
          ariaLabel={label("provider")}
          disabled={disabled || loading}
          value={seat.provider}
          onValueChange={(value) => {
            const next = seats.find((s) => s.provider.id === value) ?? null;
            onChange({
              provider: value,
              model: "",
              effort: next?.provider.default_effort ?? "",
              account_id: "",
            });
          }}
          options={[
            { value: "", label: label("follow_agent") },
            ...seats.map((s) => ({ value: s.provider.id, label: `${providerTitle(s, t)} · ${kindSuffix(s.kind, t)}` })),
            ...(seat.provider && !active ? [{ value: seat.provider, label: seat.provider }] : []),
          ]}
        />
      </div>
      {seat.provider ? (
        <label className={miniLabel}>
          {label("model_name")}
          <input
            className={field}
            aria-label={label("model_name")}
            disabled={disabled}
            list="routine-seat-models"
            placeholder={showAllModels ? t("agent_chat.model_default") : undefined}
            value={seat.model}
            onChange={(e) => onChange({ ...seat, model: e.target.value })}
          />
          <datalist id="routine-seat-models">
            {models.map((m) => (
              <option key={m.id} value={m.id}>
                {m.label || m.id}
              </option>
            ))}
          </datalist>
        </label>
      ) : null}
      {seat.provider && efforts.length > 0 ? (
        <div className={miniLabel}>
          <span>{label("effort")}</span>
          <BrandedSelect
            className={field}
            ariaLabel={label("effort")}
            disabled={disabled}
            value={seat.effort}
            onValueChange={(value) => onChange({ ...seat, effort: value })}
            options={[{ value: "", label: t("agent_chat.model_default") }, ...efforts.map((level) => ({ value: level, label: level }))]}
          />
        </div>
      ) : null}
      {seat.provider && (active?.accounts.length ?? 0) > 0 ? (
        <div className={miniLabel}>
          <span>{label("account")}</span>
          <BrandedSelect
            className={field}
            ariaLabel={label("account")}
            disabled={disabled}
            value={seat.account_id}
            onValueChange={(value) => onChange({ ...seat, account_id: value })}
            options={[{ value: "", label: t("society.chat.model_active_account") }, ...(active?.accounts ?? []).map((a) => ({ value: a.id, label: a.label }))]}
          />
        </div>
      ) : null}
    </div>
  );
}
