/**
 * Catalog CLI name → vendor mark. Shared by the CLIs table, the composer
 * picker and the mention list so a row never has to guess which file to draw.
 *
 * Exact names, not substrings: `gh` and `glab` share no letters with their
 * companies, and a substring rule would make `gws` a Google Cloud tool.
 */

export type CliMarkRender = "colour" | "mono";

export interface CliVendorLogo {
  file: string;
  render: CliMarkRender;
}

export const CLI_VENDOR_LOGOS: Record<string, CliVendorLogo> = {
  aws: { file: "aws.svg", render: "mono" },
  azure: { file: "azure.svg", render: "colour" },
  cloudflare: { file: "cloudflare.svg", render: "mono" },
  docker: { file: "docker.svg", render: "colour" },
  firebase: { file: "firebase.svg", render: "colour" },
  fly: { file: "fly.svg", render: "colour" },
  github: { file: "github.svg", render: "mono" },
  gitlab: { file: "gitlab.svg", render: "colour" },
  google: { file: "google.svg", render: "colour" },
  "google-cloud": { file: "google-cloud.svg", render: "colour" },
  heroku: { file: "heroku.svg", render: "colour" },
  kubernetes: { file: "kubernetes.svg", render: "colour" },
  neon: { file: "neon.svg", render: "colour" },
  netlify: { file: "netlify.svg", render: "colour" },
  planetscale: { file: "planetscale.svg", render: "mono" },
  railway: { file: "railway.svg", render: "mono" },
  render: { file: "render.svg", render: "mono" },
  stripe: { file: "stripe.svg", render: "mono" },
  supabase: { file: "supabase.svg", render: "colour" },
  twilio: { file: "twilio.svg", render: "colour" },
  vercel: { file: "vercel.svg", render: "mono" },
};

export const CLI_VENDORS: Record<string, string> = {
  aws: "aws",
  az: "azure",
  docker: "docker",
  firebase: "firebase",
  flyctl: "fly",
  gcloud: "google-cloud",
  gh: "github",
  glab: "gitlab",
  gws: "google",
  heroku: "heroku",
  kubectl: "kubernetes",
  neonctl: "neon",
  netlify: "netlify",
  pscale: "planetscale",
  railway: "railway",
  render: "render",
  stripe: "stripe",
  supabase: "supabase",
  twilio: "twilio",
  vercel: "vercel",
  wrangler: "cloudflare",
};

/** The vendor a catalog CLI name belongs to, or null for an unknown binary. */
export function cliVendor(cliName: string): string | null {
  const trimmed = cliName.trim().toLowerCase().replace(/^(?:cli[_:-])+/, "");
  return CLI_VENDORS[trimmed] ?? CLI_VENDORS[trimmed.replace(/_/g, "-")] ?? null;
}
