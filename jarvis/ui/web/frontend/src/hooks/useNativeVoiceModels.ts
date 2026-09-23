import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

/** Mirrors the package-only API. Stored files never imply voice readiness. */
export interface NativeVoiceJob {
  id: string;
  fingerprint: string;
  phase: "acquiring" | "cancelling" | "stored" | "cancelled" | "failed";
  completed_bytes: number;
  total_bytes: number;
  error: string;
}

export interface NativeVoiceEntry {
  model_id: string;
  fingerprint: string;
  label: string;
  family: string;
  languages: string[];
  download_bytes: number;
  declared_capabilities: string[];
  package_present: boolean;
  runtime_qualified: false;
  job: NativeVoiceJob | null;
}

export interface NativeVoiceLibrary {
  entries: NativeVoiceEntry[];
  active_job: NativeVoiceJob | null;
  custom_manifest_schema: Record<string, unknown>;
}

const key = ["native-voice-models"] as const;

async function request<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`/api/local-voice${path}`, {
    signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(15_000)]) : AbortSignal.timeout(15_000),
    ...(body === undefined ? {} : {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  });
  if (!response.ok) throw new Error(`Native voice request failed (${response.status})`);
  return response.json() as Promise<T>;
}

export function useNativeVoiceModels() {
  const client = useQueryClient();
  const library = useQuery({
    queryKey: key,
    queryFn: ({ signal }) => request<NativeVoiceLibrary>("/models", undefined, signal),
    staleTime: 10_000,
    refetchInterval: (query) => query.state.data?.active_job ? 1500 + Math.random() * 500 : false,
    refetchOnWindowFocus: false,
    retry: false,
  });
  const refresh = () => client.invalidateQueries({ queryKey: key });
  const acquire = useMutation({
    mutationFn: (body: { fingerprint?: string; manifest?: unknown; source_directory?: string }) =>
      request<NativeVoiceJob>("/models/acquire", body),
    onSuccess: refresh,
    onError: refresh,
  });
  const cancel = useMutation({
    mutationFn: (id: string) => request<NativeVoiceJob>(`/jobs/${encodeURIComponent(id)}/cancel`, {}),
    onSuccess: refresh,
    onError: refresh,
  });
  return { library, acquire, cancel };
}
