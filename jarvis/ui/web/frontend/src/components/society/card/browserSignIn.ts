/** Recognize Google's public rejection page without retaining login query parameters. */
export function googleSignInRejected(value: string): boolean {
  try {
    const url = new URL(value);
    return url.protocol === "https:" && url.hostname === "accounts.google.com" &&
      (!url.port || url.port === "443") && /^\/(?:v\d+\/)?signin\/rejected\/?$/.test(url.pathname);
  } catch {
    return false;
  }
}
