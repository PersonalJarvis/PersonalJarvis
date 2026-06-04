/**
 * Chat message role discriminator.
 *
 * "user"      — utterance authored by the human.
 * "assistant" — final reply produced by the main brain.
 * "system"    — diagnostic / status string from the orchestrator.
 * "preamble"  — short pre-thinking acknowledgment emitted by the Flash-Brain
 *               (AckGenerator) before the assistant's final reply lands.
 *               Rendered with muted styling and a "pre-ack" chip so the
 *               history makes the two-stage answer flow visible. See the
 *               Pre-Thinking-Ack Flash-Brain spec, §4 (UI/Frontend).
 */
export type MessageRole = "user" | "assistant" | "system" | "preamble";
