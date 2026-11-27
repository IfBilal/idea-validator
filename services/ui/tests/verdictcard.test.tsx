import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { VerdictCard } from "@/components/VerdictCard";
import { parseCard, safeHref } from "@/lib/verdict";
import example from "./fixtures/verdict_worked_example.json";

const base = example as Record<string, unknown>;
const card = (over: Record<string, unknown> = {}) => ({ ...base, ...over });

describe("settled verdict card", () => {
  it("shows verdict, stability, score, confidence, version, gates, versions and the honesty line", () => {
    render(<VerdictCard raw={card({ version: 2, cache_hit: true })} />);
    const region = screen.getByRole("region", { name: "Verdict" });
    expect(within(region).getByText("GO")).toBeInTheDocument();
    expect(within(region).getByText("BORDERLINE")).toBeInTheDocument();
    expect(within(region).getByText("70.5")).toBeInTheDocument();
    expect(within(region).getByText("0.83")).toBeInTheDocument(); // 0.825 shown to two decimals
    expect(within(region).getByText("v2")).toBeInTheDocument();
    expect(within(region).getByText("cached")).toBeInTheDocument();
    expect(within(region).getByText(/decision support, not a prediction of success/i)).toBeInTheDocument();
    for (const gate of ["Evidence", "Feasibility", "Risk", "Duplicate"]) {
      expect(within(region).getAllByText(gate, { exact: false }).length).toBeGreaterThan(0);
    }
    expect(within(region).getByText(/engine m11@v1/i)).toBeInTheDocument();
  });

  it("lists all six dimensions with the engine's adjusted scores and weights", () => {
    render(<VerdictCard raw={card()} />);
    const comp = screen.getByRole("listitem", { name: "Competition" });
    expect(within(comp).getByText("2 / 5")).toBeInTheDocument();
    expect(within(comp).getByText("weight 15")).toBeInTheDocument();
    expect(screen.getByRole("listitem", { name: "Learning value" })).toHaveTextContent("4.5 / 5");
  });

  it("reports stability from the sensitivity runs", () => {
    render(<VerdictCard raw={card()} />);
    expect(screen.getByText("4 of 14 checks change the verdict.")).toBeInTheDocument();
    expect(screen.getByText(/weight feasibility -20% → PIVOT/)).toBeInTheDocument();
  });

  it("offers 'what would make this GO' only when the verdict is not already GO", () => {
    const { rerender } = render(<VerdictCard raw={card()} />);
    expect(screen.queryByText(/what would make this go/i)).not.toBeInTheDocument();
    rerender(<VerdictCard raw={card({ verdict: "PIVOT", score: 66 })} />);
    expect(screen.getByText(/what would make this go/i)).toBeInTheDocument();
    expect(screen.getByText(/Competition 2 → 3 gives 73.5 \(GO, stable\)/)).toBeInTheDocument();
  });

  it("names the failed gate on a gate-failure NO-GO", () => {
    render(<VerdictCard raw={card({ verdict: "NO-GO", score: 40, gates: { evidence: "pass", feasibility: "fail", risk: "pass", duplicate: "pass" } })} />);
    expect(screen.getByRole("note")).toHaveTextContent("Blocked by the feasibility gate.");
  });

  it("shows an abstained dimension as 'no score' instead of 0", () => {
    render(<VerdictCard raw={card({ adjusted_scores: { ...(base.adjusted_scores as object), risk: null } })} />);
    expect(screen.getByRole("listitem", { name: "Risk" })).toHaveTextContent("no score (abstained)");
  });

  it("insufficient evidence: no GO/NO-GO, no score, and what was missing", () => {
    render(<VerdictCard raw={card({ verdict: "INSUFFICIENT_EVIDENCE", confidence: 0.42, missing: ["Found 2 of 3 required independent sources", "Confidence 0.42 below 0.50"] })} />);
    expect(screen.getByText("NOT ENOUGH EVIDENCE")).toBeInTheDocument();
    expect(screen.queryByText(/^(GO|PIVOT|NO-GO)$/)).not.toBeInTheDocument();
    expect(screen.queryByText(/\/ 100/)).not.toBeInTheDocument();
    const missing = screen.getByRole("list", { name: "What was missing" });
    expect(within(missing).getAllByRole("listitem")).toHaveLength(2);
  });
});

describe("evidence links and claims", () => {
  const claims = [
    { claim_id: "cl_competition", text: "Two tools already export Notion pages.", evidence_ids: ["card_aaaaaaaa#c0", "card_bbbbbbbb#c1"], dimension: "competition" },
  ];
  const evidence = {
    "card_aaaaaaaa#c0": { url: "https://news.ycombinator.com/item?id=1", quote: "a quote" },
    "card_bbbbbbbb#c1": { url: "javascript:alert(1)", quote: "evil" },
  };

  it("opens https sources in a new tab with noopener, and never links javascript: URLs", () => {
    render(<VerdictCard raw={card({ claims, evidence })} />);
    const link = screen.getByRole("link", { name: /aaaaaaaa#c0/ });
    expect(link).toHaveAttribute("href", "https://news.ycombinator.com/item?id=1");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(link).toHaveAttribute("title", "a quote");
    expect(screen.queryByRole("link", { name: /bbbbbbbb/ })).not.toBeInTheDocument();
    expect(screen.getByText(/bbbbbbbb#c1/)).toBeInTheDocument(); // still shown, just not clickable
  });

  it("shows Challenge only when a handler is given, and passes the claim", async () => {
    const onChallenge = vi.fn();
    const { rerender } = render(<VerdictCard raw={card({ claims, evidence })} />);
    expect(screen.queryByRole("button", { name: "Challenge" })).not.toBeInTheDocument();
    rerender(<VerdictCard raw={card({ claims, evidence })} onChallenge={onChallenge} />);
    await userEvent.click(screen.getByRole("button", { name: "Challenge" }));
    expect(onChallenge).toHaveBeenCalledWith(expect.objectContaining({ claim_id: "cl_competition" }));
  });

  it("strikes through a removed claim and hides its Challenge button", () => {
    render(<VerdictCard raw={card({ claims: [{ ...claims[0], removed: true }], evidence })} onChallenge={vi.fn()} />);
    expect(screen.getByText("Two tools already export Notion pages.")).toHaveClass("line-through");
    expect(screen.queryByRole("button", { name: "Challenge" })).not.toBeInTheDocument();
  });

  it("renders claim text as text, not HTML", () => {
    render(<VerdictCard raw={card({ claims: [{ ...claims[0], text: "<img src=x onerror=alert(1)>" }], evidence })} />);
    expect(screen.getByText("<img src=x onerror=alert(1)>")).toBeInTheDocument();
    expect(document.querySelector("img")).toBeNull();
  });
});

describe("a card without a verdict never shows one", () => {
  it("needs input: lists what is needed, no verdict words, no score", () => {
    render(<VerdictCard raw={{ verdict: null, partial: true, needs_input: ["estimated_effort_hours"], missing: ["effort estimate"] }} />);
    expect(screen.getByRole("heading", { name: /needs your input/i })).toBeInTheDocument();
    expect(screen.getByText("estimated effort hours")).toBeInTheDocument();
    expect(screen.queryByText(/GO|PIVOT|\/ 100|Score/)).not.toBeInTheDocument();
  });

  it("stopped: says why, and shows nothing the engine did not produce", () => {
    render(<VerdictCard raw={{ verdict: null, partial: true, needs_input: [], stop_reason: "token budget", scores: { risk: 3 } }} />);
    expect(screen.getByText(/Reason: token budget/)).toBeInTheDocument();
    expect(screen.queryByText(/\/ 5/)).not.toBeInTheDocument();
  });

  it("partial:true wins even if a stray verdict field is present", () => {
    render(<VerdictCard raw={{ verdict: "GO", partial: true, score: 99, needs_input: [] }} />);
    expect(screen.queryByText("GO")).not.toBeInTheDocument();
    expect(screen.queryByText("99")).not.toBeInTheDocument();
  });

  it("a missing verdict field is treated as no verdict", () => {
    expect(parseCard({ score: 80 }).kind).toBe("partial");
  });

  it.each([null, undefined, "GO", 42, [], { verdict: "MAYBE", score: 1, confidence: 1 }])("renders %j as an error, never a verdict", (raw) => {
    render(<VerdictCard raw={raw} />);
    expect(screen.queryByText(/^(GO|PIVOT|NO-GO)$/)).not.toBeInTheDocument();
  });
});

describe("safeHref", () => {
  it.each([
    ["https://a.org/x?y=1", "https://a.org/x?y=1"],
    ["http://a.org/", "http://a.org/"],
    ["javascript:alert(1)", undefined],
    ["data:text/html,hi", undefined],
    ["//a.org", undefined],
    ["not a url", undefined],
    [undefined, undefined],
  ])("%s", (input, expected) => {
    expect(safeHref(input)).toBe(expected);
  });
});
