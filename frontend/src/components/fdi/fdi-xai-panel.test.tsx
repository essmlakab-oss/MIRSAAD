import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FDIXAIPanel } from "./fdi-xai-panel";
import { ecodHour, HOUR_ISO, marchHour } from "@/test/fixtures";

vi.mock("@/lib/fdi-api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/fdi-api")>();
  return {
    ...actual,
    fetchXaiHour: vi.fn(),
    postInterpretXai: vi.fn(),
    postClassifyAttack: vi.fn(),
  };
});

import { fetchXaiHour, postClassifyAttack, postInterpretXai, type XaiHour } from "@/lib/fdi-api";

const mockFetchXaiHour = vi.mocked(fetchXaiHour);
const mockInterpret = vi.mocked(postInterpretXai);
const mockClassify = vi.mocked(postClassifyAttack);

function renderPanel(payload: unknown, runId = "run-1") {
  mockFetchXaiHour.mockResolvedValue(payload as never);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <FDIXAIPanel runId={runId} station="AZ_Tucson_11_W" hourIso={HOUR_ISO} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockInterpret.mockResolvedValue({
    resume: "Test reply",
    verdict: "ALERT",
    what_happened: "Test",
    key_factors: [],
    what_it_means: "Test",
    recommended_action: "Test",
    confidence: "high",
    attempt: 1,
    request_id: "req-test",
  } as never);
});

afterEach(() => {
  document.body.innerHTML = "";
});

describe("FDIXAIPanel — §2.2", () => {
  it("Model 2 card replaces the rule trace: no rule card is rendered anywhere", async () => {
    renderPanel(marchHour);

    expect(await screen.findByTestId("model2-block")).toBeTruthy();
    expect(screen.queryByTestId("rule-card")).toBeNull();
    expect(screen.queryByTestId("rule-no-fire")).toBeNull();
    expect(screen.queryByTestId("rule-trace-label")).toBeNull();
    expect(screen.queryByTestId("model1-toggle")).toBeNull();
  });

  it("Model 2: ATTAQUE hours lead the card and offer the on-demand classify button", async () => {
    renderPanel(marchHour);

    await waitFor(() => {
      expect(screen.getByTestId("model2-block")).toBeTruthy();
    });
    expect(screen.getByTestId("model2-classify-button").textContent).toBe("Classify with Model 2");
    expect(screen.queryByTestId("model2-verdict")).toBeNull();
  });

  it("Model 2: clicking classify reveals detected + confidence", async () => {
    const user = userEvent.setup();
    mockClassify.mockResolvedValue({ detected: true, confidence: 0.92, type: null, type_conf: null });
    renderPanel(marchHour);

    const button = await screen.findByTestId("model2-classify-button");
    await user.click(button);

    const verdict = await screen.findByTestId("model2-verdict");
    expect(verdict.textContent).toBe("ATTACK");
    expect(screen.getByTestId("model2-confidence").textContent).toContain("92%");
    expect(screen.queryByTestId("model2-classify-button")).toBeNull();
  });

  it("Model 2: low confidence still means detected=false copy, not a family", async () => {
    const user = userEvent.setup();
    mockClassify.mockResolvedValue({ detected: false, confidence: 0.12, type: null, type_conf: null });
    renderPanel(marchHour);

    const button = await screen.findByTestId("model2-classify-button");
    await user.click(button);

    const verdict = await screen.findByTestId("model2-verdict");
    expect(verdict.textContent).toBe("NORMAL");
    expect(screen.getByTestId("model2-family-note").textContent).toContain(
      "did not clear the attack threshold",
    );
  });

  it("Model 2: an honest unavailable note when the classifier answers null", async () => {
    const user = userEvent.setup();
    mockClassify.mockResolvedValue(null);
    renderPanel(marchHour);

    const button = await screen.findByTestId("model2-classify-button");
    await user.click(button);

    expect(await screen.findByTestId("model2-unavailable")).toBeTruthy();
  });

  it("Model 2: a pre-computed classification is revealed without the button", async () => {
    const classified: XaiHour = {
      ...marchHour,
      rules: {
        ...marchHour.rules,
        attack_classification: { detected: true, confidence: 0.9999, type: null, type_conf: null },
      },
    };
    renderPanel(classified);

    await screen.findByTestId("model2-verdict");
    expect(screen.getByTestId("model2-verdict").textContent).toBe("ATTACK");
    expect(screen.queryByTestId("model2-classify-button")).toBeNull();
  });

  it("Model 2: when the head answers, the attack family is named with its label", async () => {
    const classified: XaiHour = {
      ...marchHour,
      rules: {
        ...marchHour.rules,
        attack_classification: { detected: true, confidence: 0.9894, type: "fake_storm", type_conf: 0.97 },
      },
    };
    renderPanel(classified);

    await screen.findByTestId("model2-type-pill");
    expect(screen.getByTestId("model2-type-pill").textContent).toBe("Fake Storm");
    expect(screen.getByTestId("model2-verdict").textContent).toBe("ATTACK");
    expect(screen.getByTestId("model2-confidence").textContent).toContain("99%");
    expect(screen.queryByTestId("model2-family-note")!.textContent).not.toContain(
      "no family on record",
    );
  });

  it("Model 2: detected without a family stays honest (no invented type)", async () => {
    const classified: XaiHour = {
      ...marchHour,
      rules: {
        ...marchHour.rules,
        attack_classification: { detected: true, confidence: 0.5, type: null, type_conf: null },
      },
    };
    renderPanel(classified);

    await screen.findByTestId("model2-verdict");
    expect(screen.getByTestId("model2-type-pill").textContent).toBe("—");
    expect(screen.getByTestId("model2-family-note").textContent).toContain(
      "no family on record for this frame",
    );
  });

  it("Model 2: the +6 witness-sensor input contract is always on the card", async () => {
    renderPanel(marchHour);

    const contract = await screen.findByTestId("model2-input-contract");
    const text = contract.textContent!;
    for (const sensor of ["sm20", "sm50", "st10", "st20", "rh", "sur"]) {
      expect(text).toContain(sensor);
    }
    expect(text).toContain("untouched");
    expect(text).toContain("In short");
    expect(text).toContain("witnesses");
  });

  it("Model 1 rule trace is gone entirely — only the Model 2 card reads per-hour", async () => {
    renderPanel(marchHour);

    const model2 = await screen.findByTestId("model2-block");
    expect(model2).toBeTruthy();
    // even the classify button + honest copy live inside the card, no trace block
    expect(within(model2).getByTestId("model2-classify-button")).toBeTruthy();
  });

  it("Model 2: ON every hour — the classification card replaces the trace", async () => {
    renderPanel(ecodHour);

    await screen.findByTestId("model2-block");
    expect(screen.queryByTestId("rule-no-fire")).toBeNull();
  });

  it("B-3: the Model 2 card is the per-hour stack on both branches", async () => {
    renderPanel(marchHour);
    expect(await screen.findByTestId("model2-block")).toBeTruthy();

    document.body.innerHTML = "";
    renderPanel(ecodHour);
    expect(await screen.findByTestId("model2-block")).toBeTruthy();
  });

  it("exactly three tabs, exact labels", async () => {
    renderPanel(ecodHour);

    await screen.findByTestId("pill-alert-evidence");
    expect(screen.getByTestId("pill-ai-interpretation")).toBeTruthy();
    expect(screen.getByTestId("pill-temporal")).toBeTruthy();

    const labels = screen.getAllByRole("tab").map((tab) => (tab.textContent ?? "").trim());
    expect(labels).toEqual(["Twin Expectation & Alert Evidence", "AI Interpretation", "Temporal"]);
  });

  it("Tab 1: renders ECOD above the SHAP table", async () => {
    renderPanel(ecodHour);

    const ecodContainer = await screen.findByTestId("ecod-families");
    const shapTable = screen.getByTestId("twin-table");

    // Verify DOM order: ECOD first, then SHAP table below.
    expect(
      ecodContainer.compareDocumentPosition(shapTable) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();

    const cards = within(ecodContainer).getAllByTestId("ecod-family-card");
    expect(cards).toHaveLength(5);

    // Check SHAP table rows
    expect(within(shapTable).getAllByRole("row")).toHaveLength(21);
  });

  it("Tab 2: AI Interpretation renders the auto-fetched AI insight", async () => {
    const user = userEvent.setup();
    renderPanel(ecodHour);

    await screen.findByTestId("model2-block");
    await user.click(screen.getByTestId("pill-ai-interpretation"));

    expect(await screen.findByText("Executive interpretation")).toBeTruthy();
  });

  it("Tab 3: Temporal keeps the Recharts container and the verbatim §2.8 subtitle", async () => {
    const user = userEvent.setup();
    renderPanel(ecodHour);

    await screen.findByTestId("model2-block");
    await user.click(screen.getByTestId("pill-temporal"));

    await waitFor(() => {
      expect(document.querySelector('[data-tour="fdi-xai-temporal"]')).toBeTruthy();
    });
    expect(
      screen.getByText("Digital Twin expected vs observed sm5 — physical proof, not SHAP."),
    ).toBeTruthy();
  });

  it("B-4: the heading reads exactly 'Why was this hour flagged?'", async () => {
    renderPanel(ecodHour);
    await screen.findByTestId("model2-block");
    const heading = screen.getByText("Why was this hour flagged?");
    expect(heading.tagName).toBe("H2");
  });

  it("fetches the hour for (run, station, hour) exactly once", async () => {
    renderPanel(ecodHour);
    await screen.findByTestId("model2-block");
    expect(mockFetchXaiHour).toHaveBeenCalledTimes(1);
    expect(mockFetchXaiHour).toHaveBeenCalledWith("run-1", "AZ_Tucson_11_W", HOUR_ISO);
  });
});
