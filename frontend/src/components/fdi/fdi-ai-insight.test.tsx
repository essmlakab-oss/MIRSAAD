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
  };
});

import { fetchXaiHour, postInterpretXai } from "@/lib/fdi-api";

const mockFetchXaiHour = vi.mocked(fetchXaiHour);
const mockInterpret = vi.mocked(postInterpretXai);

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
  // Prevent auto-fetch from throwing unhandled rejections in tests
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
  it("Model 2 card replaces the rule trace, no rule card anywhere", async () => {
    renderPanel(marchHour);

    expect(await screen.findByTestId("model2-block")).toBeTruthy();
    expect(screen.queryByTestId("rule-card")).toBeNull();
    expect(screen.queryByTestId("rule-no-fire")).toBeNull();

    // The classification card sits above the tabs.
    const card = screen.getByTestId("model2-block");
    const pills = screen.getByTestId("pill-alert-evidence");
    expect(card.compareDocumentPosition(pills) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("exactly three tabs, exact labels", async () => {
    renderPanel(ecodHour);

    await screen.findByTestId("pill-alert-evidence");
    expect(screen.getByTestId("pill-ai-interpretation")).toBeTruthy();
    expect(screen.getByTestId("pill-temporal")).toBeTruthy();

    // exact labels — this also pins down that no further tab ever appears
    const labels = screen.getAllByRole("tab").map((tab) => (tab.textContent ?? "").trim());
    expect(labels).toEqual(["Twin Expectation & Alert Evidence", "AI Interpretation", "Temporal"]);
  });

  it("Tab 1: renders both Twin Expectation and Alert Evidence side-by-side", async () => {
    const user = userEvent.setup();
    renderPanel(ecodHour);

    // Both are visible in the default tab
    const container = await screen.findByTestId("ecod-families");
    const cards = within(container).getAllByTestId("ecod-family-card");
    expect(cards).toHaveLength(5);

    // Everything collapsed on first render.
    expect(screen.queryByTestId("ecod-family-details")).toBeNull();

    // Find by the v3 family name.
    const twinCard = cards.find((c) =>
      (c.textContent ?? "").includes("Paired-step fingerprint"),
    )!;
    expect(twinCard).toBeTruthy();

    const twinButton = within(twinCard).getByRole("button");
    expect(twinButton.getAttribute("aria-expanded")).toBe("false");

    await user.click(twinButton);
    expect(twinButton.getAttribute("aria-expanded")).toBe("true");

    const details = screen.getByTestId("ecod-family-details");
    const rows = within(details).getAllByTestId("ecod-witness-row");
    expect(rows.length).toBeGreaterThanOrEqual(2);

    // Raw key remains visible for the analyst audience.
    expect(within(details).getByText("jump_ex")).toBeTruthy();

    // Business language replaced the raw ECDF p-value: "1 in N" not "1.00e-3".
    expect(within(details).getAllByText(/1 in \d/).length).toBeGreaterThan(0);
    expect(within(details).queryByText(/^1\.\d+e-?\d+$/)).toBeNull();

    // Clicking again collapses.
    await user.click(twinButton);
    expect(screen.queryByTestId("ecod-family-details")).toBeNull();

    // Check TwinTable is also rendered
    const table = screen.getByTestId("twin-table");
    expect(within(table).getAllByRole("row")).toHaveLength(21); // header + 20
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

  it("B-3: the Model 2 card is the per-hour stack on both branches", async () => {
    renderPanel(marchHour);
    expect(await screen.findByTestId("model2-block")).toBeTruthy();

    document.body.innerHTML = "";
    renderPanel(ecodHour);
    expect(await screen.findByTestId("model2-block")).toBeTruthy();
  });

  it("fetches the hour for (run, station, hour) exactly once", async () => {
    renderPanel(ecodHour);
    await screen.findByTestId("model2-block");
    expect(mockFetchXaiHour).toHaveBeenCalledTimes(1);
    expect(mockFetchXaiHour).toHaveBeenCalledWith("run-1", "AZ_Tucson_11_W", HOUR_ISO);
  });
});
