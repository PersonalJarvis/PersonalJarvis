export interface ApiParameter {
  name: string;
  location: "path" | "query" | "header";
  type: "string" | "integer" | "number" | "boolean" | "array" | "object";
  value_schema?: Record<string, unknown> | null;
  required: boolean;
  description: string;
}
export interface ApiAction {
  id: string;
  description: string;
  method: "GET" | "POST" | "PUT" | "PATCH" | "DELETE" | "HEAD" | "OPTIONS";
  path: string;
  parameters: ApiParameter[];
  body_schema: Record<string, unknown> | null;
  body_required?: boolean;
  body_encoding?: "json" | "multipart" | "form" | "binary";
  file_fields?: string[];
  content_type?: string;
  risk_tier: "monitor" | "ask" | "block";
  response: "auto" | "json" | "text" | "file";
}
export interface ApiDefinition {
  id: string;
  name: string;
  description: string;
  base_url: string;
  auth: { mode: "none" | "bearer" | "header" | "query"; header_name: string };
  enabled: boolean;
  actions: ApiAction[];
}
export interface ApiStatus {
  definition: ApiDefinition;
  has_credential: boolean;
  tools_ready: boolean;
}
export interface ApiConnection {
  id: string;
  name: string;
  website: string;
  brand_id: string;
  logo_data: string;
  categories: string[];
  action_count: number;
  omitted_operations: number;
  enabled: boolean;
  has_credential: boolean;
  tools_ready: boolean;
  status: "configured" | "verified" | "limited";
}
export class ApiConnectionError extends Error {
  constructor(public code: string, public suggestions: string[] = []) { super(code); }
}
export async function customApiRequest<T>(path = "", init?: RequestInit): Promise<T> {
  const response = await fetch(`/api/custom-apis${path}`, init);
  const data = await response.json();
  if (!response.ok) {
    if (data.detail?.code) throw new ApiConnectionError(data.detail.code, data.detail.suggestions ?? []);
    throw new Error(typeof data.detail === "string" ? data.detail : `HTTP ${response.status}`);
  }
  return data as T;
}
export function newApiDefinition(): ApiDefinition {
  return {
    id: crypto.randomUUID().replaceAll("-", ""), name: "", description: "", base_url: "",
    auth: { mode: "bearer", header_name: "X-API-Key" }, enabled: true,
    actions: [newApiAction()],
  };
}
export function newApiAction(index = 1): ApiAction {
  return { id: `action_${index}`, description: "", method: "GET", path: "/", parameters: [],
    body_schema: null, risk_tier: "monitor", response: "auto" };
}
export function actionWithPath(action: ApiAction, path: string): ApiAction {
  const names = [...new Set([...path.matchAll(/\{([a-zA-Z_][a-zA-Z0-9_]*)\}/g)].map((m) => m[1]))];
  return { ...action, path, parameters: [
    ...action.parameters.filter((p) => p.location === "query"),
    ...names.map((name): ApiParameter => action.parameters.find((p) => p.location === "path" && p.name === name)
      ?? { name, location: "path", type: "string", required: true, description: "" }),
  ] };
}
