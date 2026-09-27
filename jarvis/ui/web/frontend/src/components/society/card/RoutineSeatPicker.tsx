/** The model seat one routine runs on — pinned at creation, changeable here. */
import { useT } from "@/i18n";
import { useAgentChat } from "@/components/agentchat/AgentChatStoreContext";
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
      <label className={miniLabel}>
        {label("provider")}
        <select
          className={field}
          aria-label={label("provider")}
          disabled={disabled || loading}
          value={seat.provider}
          onChange={(e) => {
            const next = seats.find((s) => s.provider.id === e.target.value) ?? null;
            onChange({
              provider: e.target.value,
              model: "",
              effort: next?.provider.default_effort ?? "",
              account_id: "",
            });
          }}
        >
          <option value="">{label("follow_agent")}</option>
          {seats.map((s) => (
            <option key={s.provider.id} value={s.provider.id}>
              {`${providerTitle(s, t)} · ${kindSuffix(s.kind, t)}`}
            </option>
          ))}
          {seat.provider && !active ? <option value={seat.provider}>{seat.provider}</option> : null}
        </select>
      </label>
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
        <label className={miniLabel}>
          {label("effort")}
          <select
            className={field}
            aria-label={label("effort")}
            disabled={disabled}
            value={seat.effort}
            onChange={(e) => onChange({ ...seat, effort: e.target.value })}
          >
            <option value="">{t("agent_chat.model_default")}</option>
            {efforts.map((level) => (
              <option key={level} value={level}>
                {level}
              </option>
            ))}
          </select>
        </label>
      ) : null}
      {seat.provider && (active?.accounts.length ?? 0) > 0 ? (
        <label className={miniLabel}>
          {label("account")}
          <select
            className={field}
            aria-label={label("account")}
            disabled={disabled}
            value={seat.account_id}
            onChange={(e) => onChange({ ...seat, account_id: e.target.value })}
          >
            <option value="">{t("society.chat.model_active_account")}</option>
            {(active?.accounts ?? []).map((a) => (
              <option key={a.id} value={a.id}>
                {a.label}
              </option>
            ))}
          </select>
        </label>
      ) : null}
    </div>
  );
}
