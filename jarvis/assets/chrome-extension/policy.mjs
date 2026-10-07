// This module has no Chrome dependency so boundary cases are testable with Node.
export function serverOrigin(value) {
  const url = new URL(value);
  if (url.protocol !== "http:" || url.hostname !== "127.0.0.1" || url.username || url.password
      || (url.pathname !== "/" && url.pathname !== "") || url.search || url.hash) {
    throw new Error("Use the local Jarvis address: http://127.0.0.1:PORT");
  }
  return url.origin;
}

export function domains(values) {
  if (!Array.isArray(values) || values.length > 100) throw new Error("Invalid website permissions");
  return [...new Set(values.map((value) => {
    if (typeof value !== "string" || value.length > 253 || !/^[a-z0-9.-]+$/i.test(value)
        || value.includes("..") || value.startsWith(".") || value.endsWith(".")
        || !value.includes(".") || /(^|\.)(localhost|local|internal)$/.test(value)
        || /^\d+(\.\d+)*$/.test(value)) throw new Error("Invalid website permission");
    return value.toLowerCase();
  }))];
}

export function allowedUrl(value, allowed, blank = false) {
  if (blank && value === "about:blank") return true;
  try {
    if (typeof value !== "string" || /[\u0000-\u0020\\]/.test(value)) return false;
    const url = new URL(value);
    return ["https:", "http:"].includes(url.protocol) && !url.username && !url.password
      && !url.port && allowed.some((host) => url.hostname === host || url.hostname.endsWith(`.${host}`));
  } catch { return false; }
}

export function actionShape(action, manual = false) {
  if (!action || typeof action !== "object" || Array.isArray(action)
      || Object.keys(action).length !== 1) throw new Error("Expected one action");
  const [op, args] = Object.entries(action)[0];
  const fields = manual
    ? {navigate: ["url"], click: ["x", "y"], text: ["text"], key: ["key"], scroll: ["dx", "dy"], back: [], reload: []}
    : {navigate: ["url"], click: ["index"], input: ["index", "text"], scroll: ["dy"], wait: []};
  if (!(op in fields) || !args || typeof args !== "object" || Array.isArray(args)
      || Object.keys(args).length !== fields[op].length
      || Object.keys(args).some((key) => !fields[op].includes(key))) throw new Error("Unsupported action");
  if ("index" in args && (!Number.isInteger(args.index) || args.index < 1 || args.index > 200)) {
    throw new Error("Invalid element");
  }
  for (const key of ["x", "y", "dx", "dy"]) {
    if (key in args && (!Number.isFinite(args[key]) || Math.abs(args[key]) > 10000)) throw new Error("Invalid position");
  }
  for (const key of ["url", "text", "key"]) {
    if (key in args && (typeof args[key] !== "string" || args[key].length > 10000)) throw new Error("Invalid text");
  }
  return [op, args];
}

export function retryDelay(attempt, random = Math.random) {
  return Math.round(Math.min(60000, 1000 * 2 ** Math.min(attempt, 6)) * (0.75 + random() * 0.5));
}

export function pairingCode(value) {
  if (typeof value !== "string" || !/^[A-Za-z0-9_-]{6,64}$/.test(value)) {
    throw new Error("Invalid pairing code");
  }
  return value;
}
