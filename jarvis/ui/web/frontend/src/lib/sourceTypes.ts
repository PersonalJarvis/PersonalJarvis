export type SourceKind = "manual" | "chat" | "form" | "mcp" | "sse" | "kafka" | "rabbitmq" | "mqtt" | "redis" | "file" | "workflow";
export interface SourceSettings { kind: SourceKind; [key: string]: unknown; }
export const DEFAULT_MANUAL_SOURCE: SourceSettings = { kind: "manual" };
