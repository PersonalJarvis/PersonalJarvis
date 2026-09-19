/** Client preflight before draft recovery storage. Server guard remains authoritative. */
const PATTERNS = [
  /\bsk-(?:proj-|ant-|or-|live-)?[A-Za-z0-9_-]{20,}\b/,
  /\b(?:AIza[0-9A-Za-z_-]{30,}|xai-[A-Za-z0-9]{20,}|gsk_[A-Za-z0-9]{20,}|gh[pousr]_[A-Za-z0-9]{30,})\b/,
  /\b[Bb]earer\s+[A-Za-z0-9._\-]{16,}\b/,
  /^\s*authorization\s*[:=]\s*\S{12,}\s*$/im,
  /\b(?:api[_-]?key|secret(?:[_-]?key)?|password|passwd|pwd|access[_-]?token|auth[_-]?token|client[_-]?secret)\b\s*[:=]\s*['"]?[^\s'"]{8,}/i,
  /\beyJ[A-Za-z0-9_-]{6,}\.eyJ[A-Za-z0-9_-]{3,}\.[A-Za-z0-9_-]{3,}\b/,
  /\b[0-9a-fA-F]{64,}\b/,
  /\b[A-Za-z0-9+/]{64,}={0,2}\b/,
  /-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----/,
];
export function containsCredential(value: string): boolean {
  return PATTERNS.some((pattern) => pattern.test(value));
}

