import type { AccountUsage } from "./agentAccountsApi";

export interface ResetCredit {
  id: string;
  title: string | null;
  description: string | null;
  expires_at: string | null;
  redeemable: boolean;
}

export interface AccountResets {
  account_id: string;
  status: "ok" | "browser" | "unsupported" | "unavailable";
  available_count: number | null;
  credits: ResetCredit[] | null;
  can_redeem: boolean;
  account_key: string | null;
  manage_url: string | null;
  as_of: number;
  usage: AccountUsage | null;
}

export interface ResetAttempt {
  idempotency_key: string;
  credit_id: string | null;
  account_key: string;
}

export interface ResetResult {
  outcome: "reset" | "alreadyRedeemed" | "nothingToReset" | "noCredit";
  resets: AccountResets;
}

export class ResetRequestError extends Error {
  constructor(public code: string) { super(code); }
}

async function answer<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    const code = body?.detail?.code;
    throw new ResetRequestError(typeof code === "string" ? code : "uncertain");
  }
  return response.json() as Promise<T>;
}

export async function fetchAccountResets(accountId: string): Promise<AccountResets> {
  return answer(await fetch(`/api/agent-accounts/${encodeURIComponent(accountId)}/resets`, { cache: "no-store" }));
}

export async function consumeAccountReset(accountId: string, attempt: ResetAttempt): Promise<ResetResult> {
  const result = await answer<ResetResult>(await fetch(`/api/agent-accounts/${encodeURIComponent(accountId)}/resets/consume`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...attempt, confirmed: true }),
  }));
  if (!["reset", "alreadyRedeemed", "nothingToReset", "noCredit"].includes(result.outcome) || !result.resets) {
    throw new ResetRequestError("uncertain");
  }
  return result;
}
