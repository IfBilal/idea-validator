import { z } from "zod";

/** Runtime configuration injected by /config.js (see public/config.js and the nginx entrypoint). */
const ConfigSchema = z.object({
  // "" (same origin, root), a path such as "/api" (same origin behind the ingress), or an absolute
  // http(s) URL. Trailing slashes are removed. Anything else (javascript:, ftp:, bare hosts) is refused.
  API_BASE_URL: z
    .string()
    .refine(
      (v) => v === "" || /^\/(?!\/)[\w\-./]*$/.test(v) || /^https?:\/\/[^\s/]+(:\d+)?(\/\S*)?$/.test(v),
      "API_BASE_URL must be empty, a path like /api, or an http(s) URL",
    )
    .transform((v) => v.replace(/\/+$/, "")),
});

export type AppConfig = z.infer<typeof ConfigSchema>;

export function loadConfig(raw: unknown = window.__APP_CONFIG__): AppConfig {
  const parsed = ConfigSchema.safeParse(raw);
  if (!parsed.success) {
    throw new Error(`Invalid /config.js: ${parsed.error.issues.map((i) => i.message).join("; ")}`);
  }
  return parsed.data;
}
