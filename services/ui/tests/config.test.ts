import { describe, expect, it } from "vitest";
import { loadConfig } from "@/lib/config";

describe("runtime config", () => {
  it("accepts an empty value as same origin", () => {
    expect(loadConfig({ API_BASE_URL: "" }).API_BASE_URL).toBe("");
  });

  it("accepts a same-origin path and strips its trailing slash", () => {
    expect(loadConfig({ API_BASE_URL: "/api/" }).API_BASE_URL).toBe("/api");
  });

  it("strips trailing slashes from an absolute URL", () => {
    expect(loadConfig({ API_BASE_URL: "https://api.example.org///" }).API_BASE_URL).toBe(
      "https://api.example.org",
    );
  });

  it.each(["ftp://x.org", "javascript:alert(1)", "api.example.org", "http://", "https://a b.org", "//evil.org", "/a b"])(
    "rejects %s",
    (bad) => {
      expect(() => loadConfig({ API_BASE_URL: bad })).toThrow(/Invalid \/config\.js/);
    },
  );

  it("rejects a missing or malformed config object", () => {
    expect(() => loadConfig(undefined)).toThrow();
    expect(() => loadConfig({ API_BASE_URL: 5 })).toThrow();
  });

  it("reads window.__APP_CONFIG__ by default", () => {
    window.__APP_CONFIG__ = { API_BASE_URL: "http://gw.test:8000" };
    expect(loadConfig().API_BASE_URL).toBe("http://gw.test:8000");
  });
});
