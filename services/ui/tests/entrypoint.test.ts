import { spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const SCRIPT = join(__dirname, "..", "nginx", "40-runtime-config.sh");

function run(apiBaseUrl: string | undefined) {
  const dir = mkdtempSync(join(tmpdir(), "ui-entry-"));
  const env: NodeJS.ProcessEnv = { ...process.env, CONFIG_OUT_DIR: dir };
  if (apiBaseUrl === undefined) delete env.API_BASE_URL;
  else env.API_BASE_URL = apiBaseUrl;
  const result = spawnSync("sh", [SCRIPT.replaceAll("\\", "/")], { env, encoding: "utf8" });
  const read = (f: string) => {
    try {
      return readFileSync(join(dir, f), "utf8");
    } catch {
      return null;
    }
  };
  return { status: result.status, stderr: result.stderr, config: read("config.js"), headers: read("headers.conf") };
}

describe("runtime config entrypoint", () => {
  it("defaults to the same-origin /api path", () => {
    const r = run(undefined);
    expect(r.status).toBe(0);
    expect(r.config).toContain('API_BASE_URL: "/api"');
    expect(r.headers).toContain("connect-src 'self' ;");
  });

  it("allows a cross-origin API only in connect-src", () => {
    const r = run("https://api.example.org:8443/v1");
    expect(r.status).toBe(0);
    expect(r.config).toContain('API_BASE_URL: "https://api.example.org:8443/v1"');
    expect(r.headers).toContain("connect-src 'self' https://api.example.org:8443;");
  });

  it("always sends the security headers", () => {
    const r = run("/api");
    for (const h of ["Content-Security-Policy", "X-Content-Type-Options", "X-Frame-Options", "Referrer-Policy"]) {
      expect(r.headers).toContain(h);
    }
    expect(r.headers).toContain("frame-ancestors 'none'");
    expect(r.headers).toContain("script-src 'self';");
  });

  it.each([
    '/api"; alert(1); "',
    "//evil.example",
    "javascript:alert(1)",
    "ftp://x.org",
    "https://a.org/<script>",
    "/api\nadd_header X-Evil 1;",
    "api.example.org",
  ])("refuses %j and writes nothing", (bad) => {
    const r = run(bad);
    expect(r.status).not.toBe(0);
    expect(r.config).toBeNull();
    expect(r.headers).toBeNull();
  });
});
