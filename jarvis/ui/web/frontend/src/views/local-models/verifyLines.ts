/**
 * The proof, one line per step — "Server — OK · 12 ms", "Chat — OK ·
 * gemma4:12b-it-qat · 1.8 s", "Embeddings — not tested · No embedding role
 * is configured." — shared by the overview's set-up summary and the Server
 * tab's "Run a check", so both read the same way.
 */
import type { VerifyResponse, VerifyStep } from "@/hooks/useLocalModels";
import { fill } from "@/i18n";

type T = (key: string) => string;

function stepLabel(step: VerifyStep, t: T): string {
  switch (step.id) {
    case "server":
      return t("local_models.verify.server");
    case "chat":
      return t("local_models.verify.chat");
    case "embedding":
      return t("local_models.verify.embedding");
    default:
      return step.id;
  }
}

function duration(ms: number, t: T): string {
  if (ms <= 0) return "";
  return ms >= 1000
    ? `${(ms / 1000).toFixed(1)} s`
    : fill(t("local_models.verify.ms"), { ms });
}

/** One line per step; `[]` when there is no proof yet. */
export function verifyLines(verify: VerifyResponse | undefined, t: T): string[] {
  if (!verify) return [];
  return verify.steps.map((step) => {
    const verdict =
      step.ok === null
        ? t("local_models.verify.skipped")
        : step.ok
          ? t("local_models.verify.ok")
          : t("local_models.verify.failed");
    const parts = [
      step.model,
      step.ok ? duration(step.ms, t) : "",
      step.ok === true ? "" : step.detail,
    ].filter(Boolean);
    return `${stepLabel(step, t)} — ${verdict}${
      parts.length > 0 ? ` · ${parts.join(" · ")}` : ""
    }`;
  });
}
