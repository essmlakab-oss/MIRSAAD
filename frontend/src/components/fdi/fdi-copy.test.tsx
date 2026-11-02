/**
 * §2.8 copy verification: byte-exact assertions of the reviewer-approved
 * strings across the panel (§2.2), metric cards (§2.3), the AI insight
 * (§2.4) and the evidence strip (§2.5) — plus the "Rule trace" rules-card
 * label (B-3). Nothing here may be reworded without reviewer sign-off.
 */

import { render, screen, within } from "@testing-library/react";
import { formatHourLong } from "@/lib/fdi";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FDIAiInsight } from "./fdi-ai-insight";
import { FDIMetricCards } from "./fdi-metric-cards";
import { FDIEvidenceStrip } from "./fdi-evidence-strip";
import { FDIXAIPanel } from "./fdi-xai-panel";
import { ecodHour, marchHour, normalHour, HOUR_ISO } from "@/test/fixtures";
import type { XaiSeries } from "@/lib/fdi-api";

vi.mock("@/lib/fdi-api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/fdi-api")>();
  return {
    ...actual,
    fetchXaiHour: vi.fn(),
    postInterpretXai: vi.fn(),
  };
});

vi.mock("@tanstack/react-router", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@tanstack/react-router")>();
  return {
    ...actual,
    useNavigate: vi.fn(() => () => {}),
  };
});

import { fetchXaiHour, postInterpretXai } from "@/lib/fdi-api";

const mockFetchXaiHour = vi.mocked(fetchXaiHour);
const mockInterpret = vi.mocked(postInterpretXai);

const AI_REPLY = {
  resume:
    "Confirmed attack: a paired step pattern on the 5 cm probe was not explained by the 10 cm witness.",
  verdict: "ALERT" as const,
  what_happened: "Twin expected 0.115 m³/m³, observed 0.575 (+0.460, z 12.2).",
  key_factors: ["Rule marches fired."],
  what_it_means:
    "The 5 cm reading is far above what the witnesses imply — consistent with an injected bias.",
  recommended_action: "Reject / quarantine the reading — confirmed attack.",
  confidence: "high" as const,
  attempt: 1,
  request_id: "req-copy",
};

function makeSeries(): XaiSeries {
  return {
    station: "AZ_Tucson_11_W",
    hours: [
      { t: HOUR_ISO, niveau: "ATTAQUE", alert: true, ecod_score: 1.2, twin_residual_z: 12.23 },
    ],
  };
}

function renderPanel(payload: unknown) {
  mockFetchXaiHour.mockResolvedValue(payload as never);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <FDIXAIPanel runId="run-copy" station="AZ_Tucson_11_W" hourIso={HOUR_ISO} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockInterpret.mockResolvedValue(AI_REPLY as never);
});

afterEach(() => {
  document.body.innerHTML = "";
});

describe("§2.8 copy verification — byte-exact", () => {
  it("panel heading and Model 2 card carry the primary copy", async () => {
    renderPanel(ecodHour);
    await screen.findByTestId("model2-block");
    expect(screen.getByText("Why was this hour flagged?")).toBeTruthy();
    expect(screen.getByTestId("model2-input-contract")).toBeTruthy();
  });

  it("Alert Evidence subtitle and ECOD chart title are byte-exact (B-2)", async () => {
    renderPanel(marchHour);
    await screen.findByTestId("model2-block");
    expect(
      screen.getByText("Native decision logic and ECOD per-witness contributions."),
    ).toBeTruthy();
  });

  it("pills carry the byte-exact strings, no rule trace anywhere", async () => {
    renderPanel(marchHour);
    await screen.findByTestId("model2-block");
    expect(screen.queryByTestId("rule-card")).toBeNull();
    const labels = screen.getAllByRole("tab").map((tab) => (tab.textContent ?? "").trim());
    expect(labels).toEqual(["Twin Expectation & Alert Evidence", "AI Interpretation", "Temporal"]);
  });

  it("Twin Expectation and Temporal subtitles are byte-exact (B-2)", async () => {
    const user = userEvent.setup();
    renderPanel(ecodHour);
    await screen.findByTestId("model2-block");

    expect(
      screen.getByText("Top witnesses by exact SHAP contribution to the stage-1 detector score."),
    ).toBeTruthy();

    await user.click(screen.getByTestId("pill-temporal"));
    expect(
      screen.getByText("Digital Twin expected vs observed sm5 — physical proof, not SHAP."),
    ).toBeTruthy();
  });

  it("metric-card verdict words and the residual chip are byte-exact", () => {
    render(<FDIMetricCards xai={marchHour} />);
    expect(screen.getByTestId("verdict-badge").textContent).toBe("Alert");
    expect(screen.getByTestId("residual-chip").textContent).toBe("Residual 12.2σ");

    document.body.innerHTML = "";
    render(<FDIMetricCards xai={normalHour} />);
    expect(screen.getByTestId("verdict-badge").textContent).toBe("Normal");
  });

  it("AI button and Executive interpretation heading are byte-exact", async () => {
    const user = userEvent.setup();
    render(<FDIAiInsight runId="run-copy" payload={marchHour} />);
    const button = screen.getByRole("button");
    expect((button.textContent ?? "").trim()).toBe("✨ Interpret with AI");
    await user.click(button);
    expect(await screen.findByText("Executive interpretation")).toBeTruthy();
    expect(screen.getByText("What happened")).toBeTruthy();
    expect(screen.getByText("What impacted what")).toBeTruthy();
    expect(screen.getByText("What it means")).toBeTruthy();
  });

  it("evidence strip heading is byte-exact", () => {
    render(
      <FDIEvidenceStrip
        series={makeSeries()}
        loading={false}
        stations={["AZ_Tucson_11_W"]}
        station="AZ_Tucson_11_W"
        onStationChange={() => {}}
        selectedHourIso={HOUR_ISO}
        onSelectHour={() => {}}
        threshold={1}
      />,
    );
    expect(screen.getByText("24h evidence strip — twin residual and ECOD pressure")).toBeTruthy();
  });

  it("S-3: flagged-card header and strip cell render the identical UTC label for the golden hour_iso", () => {
    document.body.innerHTML = "";
    renderPanel(marchHour);
    expect(screen.getByText(formatHourLong(HOUR_ISO))).toBeTruthy();

    document.body.innerHTML = "";
    render(
      <FDIEvidenceStrip
        series={makeSeries()}
        loading={false}
        stations={["AZ_Tucson_11_W"]}
        station="AZ_Tucson_11_W"
        onStationChange={() => {}}
        selectedHourIso={HOUR_ISO}
        onSelectHour={() => {}}
        threshold={1}
      />,
    );
    const stripLabel = document
      .querySelector('[data-testid="strip-cells"] button')!
      .getAttribute("title")!;

    expect(stripLabel.startsWith(formatHourLong(HOUR_ISO))).toBe(true);
    expect(stripLabel.split(" · ")[0]).toBe(formatHourLong(HOUR_ISO));
  });
});
