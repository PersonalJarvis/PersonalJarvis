/**
 * Step 2 — "How should {name} connect?": only the ways that work for the
 * chosen provider, the best one marked and pre-selected.
 */
import { Cloud, FileKey2, KeyRound, LockKeyhole } from "lucide-react";
import { useT } from "@/i18n";
import type { ProviderInfo } from "@/lib/computersApi";
import { OptionCard, type Method } from "./shared";

/** The methods a provider offers, best first. */
export function methodsFor(provider: ProviderInfo): Method[] {
  const methods: Method[] = [];
  if (provider.api) methods.push("api");
  methods.push("password", "private_key", "jarvis_key");
  return methods;
}

/** The method to pre-select: API import when the provider plants keys itself. */
export function recommendedMethod(provider: ProviderInfo): Method {
  return provider.api?.attaches_keys ? "api" : "password";
}

export function MethodStep({
  provider,
  value,
  onChange,
}: {
  provider: ProviderInfo;
  value: Method;
  onChange: (method: Method) => void;
}) {
  const t = useT();
  const best = recommendedMethod(provider);
  const fill = (key: string) => t(key).replace("{provider}", provider.name);
  const icons: Record<Method, JSX.Element> = {
    api: <Cloud />,
    password: <LockKeyhole />,
    private_key: <FileKey2 />,
    jarvis_key: <KeyRound />,
  };
  return (
    <div role="radiogroup" aria-label={t("computers.wz_step_method")} className="space-y-2.5">
      {methodsFor(provider).map((method) => (
        <OptionCard
          key={method}
          testId={`wz-method-${method}`}
          active={value === method}
          onClick={() => onChange(method)}
          icon={icons[method]}
          title={fill(`computers.wz_m_${method}_title`)}
          body={fill(
            method === "api" && provider.api?.attaches_keys
              ? "computers.wz_m_api_body_keys"
              : `computers.wz_m_${method}_body`,
          )}
          badge={method === best ? t("computers.recommended") : undefined}
        />
      ))}
    </div>
  );
}
