import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { FDIMetricCards } from "./fdi-metric-cards";
import { marchHour, normalHour, ecodHour } from "@/test/fixtures";
import type { XaiHour } from "@/lib/fdi-api";

describe("FDIMetricCards — §2.3", () => {
  it("shows Expected | Observed | Deviation % for the hour", () => {
    render(<FDIMetricCards xai={marchHour} />);

    expect(screen.getByText("Expected (Twin)")).toBeTruthy();
    expect(screen.getByText("0.1150")).toBeTruthy();
    expect(screen.getByText("Observed (Sensor)")).toBeTruthy();
    expect(screen.getByText("0.5750")).toBeTruthy();
    expect(screen.getByText("Deviation")).toBeTruthy();
    expect(screen.getByText("+400.0%")).toBeTruthy();
  });

  it("Rendered the Alert badge in red for ATTAQUE and the residual-σ chip at |z| >= 3", () => {
    render(<FDIMetricCards xai={marchHour} />);

    const badge = screen.getByTestId("verdict-badge");
    expect(badge.textContent).toBe("Alert");
    // jsdom normalizes inline colors to rgb()
    expect(badge.style.color).toBe("rgb(239, 68, 68)");

    const chip = screen.getByTestId("residual-chip");
    expect(chip.textContent).toBe("Residual 12.2σ");
  });

  it("S-1: flagged hour with |z| >= 3 shows the chip; normal z=1.62 hides it", () => {
    // Golden attack hour (ATTAQUE, z=12.2): chip shown — covered above and
    // re-asserted here against the niveau gate.
    const { unmount } = render(<FDIMetricCards xai={marchHour} />);
    expect(screen.getByTestId("residual-chip").textContent).toBe("Residual 12.2σ");
    unmount();
    document.body.innerHTML = "";

    // Normal hour with a moderate residual (z=1.62): hidden — |z| < 3.
    const normalModerate: XaiHour = {
      ...normalHour,
      summary: { ...normalHour.summary, twin_residual_z: 1.62 },
    };
    render(<FDIMetricCards xai={normalModerate} />);
    expect(screen.getByTestId("verdict-badge").textContent).toBe("Normal");
    expect(screen.queryByTestId("residual-chip")).toBeNull();
  });

  it("S-1: normal hour with a huge residual (z=15) still hides the chip — niveau gates it", () => {
    // On synthetic demo data normal hours can carry out-of-distribution z
    // (artifact sigma AZ=0.003957); the niveau condition must keep the chip
    // off on unflagged hours regardless of |z|.
    const normalExtreme: XaiHour = {
      ...normalHour,
      summary: { ...normalHour.summary, twin_residual_z: 15 },
    };
    render(<FDIMetricCards xai={normalExtreme} />);

    expect(screen.getByTestId("verdict-badge").textContent).toBe("Normal");
    expect(screen.queryByTestId("residual-chip")).toBeNull();
  });

  it("S-1: SUSPECT hours follow the same gate — hidden at z=1.2, shown at z=15", () => {
    // SUSPECT is the other flagged niveau in the gate: |z| < 3 hides, |z| >= 3 shows.
    const { unmount } = render(
      <FDIMetricCards
        xai={{ ...ecodHour, summary: { ...ecodHour.summary, twin_residual_z: 1.2 } }}
      />,
    );
    expect(screen.getByTestId("verdict-badge").textContent).toBe("Suspect");
    expect(screen.queryByTestId("residual-chip")).toBeNull();
    unmount();
    document.body.innerHTML = "";

    render(
      <FDIMetricCards
        xai={{ ...ecodHour, summary: { ...ecodHour.summary, twin_residual_z: 15 } }}
      />,
    );
    expect(screen.getByTestId("residual-chip").textContent).toBe("Residual 15.0σ");
  });

  it("shows the demo-only ground-truth chip and omits it for uploads (null truth)", () => {
    render(<FDIMetricCards xai={marchHour} />);
    expect(screen.getByTestId("truth-chip").textContent).toContain(
      "Attack inserted · demo ground truth",
    );
  });

  it("Normal payload without ground truth: no truth chip", () => {
    const noTruth: XaiHour = {
      ...normalHour,
      meta: { ...normalHour.meta, ground_truth: null },
    };
    render(<FDIMetricCards xai={noTruth} />);
    expect(screen.queryByTestId("truth-chip")).toBeNull();
  });
});
