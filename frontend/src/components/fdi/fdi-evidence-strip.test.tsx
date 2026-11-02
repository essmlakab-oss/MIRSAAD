import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { FDIEvidenceStrip } from "./fdi-evidence-strip";
import type { XaiSeries } from "@/lib/fdi-api";

/** S-2: the additive top_ecod_family rides through until the type regen. */
type HourWithFamily = XaiSeries["hours"][number] & { top_ecod_family?: string | null };

function makeSeries(hours: HourWithFamily[]): XaiSeries {
  return { station: "AZ_Tucson_11_W", hours };
}

const T1 = "2025-03-28T22:00:00Z";
const T2 = "2025-03-28T23:00:00Z";
const T3 = "2025-03-29T00:00:00Z";

const series = makeSeries([
  {
    t: T1,
    niveau: "historique insuffisant",
    alert: false,
    ecod_score: null,
    twin_residual_z: null,
  },
  {
    t: T2,
    niveau: "normal",
    alert: false,
    ecod_score: 0.5,
    twin_residual_z: 0.4,
    top_ecod_family: null,
  },
  {
    t: T3,
    niveau: "ATTAQUE",
    alert: true,
    ecod_score: 8,
    twin_residual_z: 6,
    top_ecod_family: "Paired-step fingerprint",
  },
]);

function renderStrip(props: Partial<Parameters<typeof FDIEvidenceStrip>[0]> = {}) {
  return render(
    <FDIEvidenceStrip
      series={series}
      loading={false}
      stations={["AZ_Tucson_11_W"]}
      station="AZ_Tucson_11_W"
      onStationChange={() => {}}
      selectedHourIso={T2}
      onSelectHour={() => {}}
      threshold={1}
      {...props}
    />,
  );
}

const CELL_MARGIN = 4; // MIN_BAR: pressure 0 → 4px tall

describe("FDIEvidenceStrip — §2.5", () => {
  it("renders one cell per hour, colored by niveau, with the verbatim title", () => {
    const { container } = renderStrip();

    expect(screen.getByText("24h evidence strip — twin residual and ECOD pressure")).toBeTruthy();

    const cells = container.querySelectorAll('[data-testid="strip-cells"] button');
    expect(cells).toHaveLength(3);

    const [hist, normal, attack] = Array.from(cells) as HTMLElement[];
    expect(hist.getAttribute("aria-label")).toContain("No history");
    expect(normal.getAttribute("aria-label")).toContain("Normal");
    expect(normal.getAttribute("aria-label")).toContain("score 0.500");
    expect(attack.getAttribute("aria-label")).toContain("Attack");
    expect(attack.getAttribute("aria-label")).toContain("twin residual z 6.00");
  });

  it("S-2: hover tooltip carries top_ecod_family — non-null on an ATTAQUE cell, null on a normal cell", () => {
    const { container } = renderStrip();

    const cells = container.querySelectorAll('[data-testid="strip-cells"] button');
    const [, normal, attack] = Array.from(cells) as HTMLElement[];

    // the ATTAQUE hour must show its dominant ECOD family
    expect(attack.getAttribute("title")).toContain("family Paired-step fingerprint");
    expect(attack.getAttribute("aria-label")).toContain("family Paired-step fingerprint");
    // a perfectly normal hour carries no signal — the family is null
    expect(normal.getAttribute("title")).toContain("family —");
    expect(normal.getAttribute("aria-label")).toContain("family —");
  });

  it("cell heights encode the pressure formula (min(1, max(z/5, score/2t)))", () => {
    const { container } = renderStrip({ selectedHourIso: null });

    const cells = Array.from(
      container.querySelectorAll('[data-testid="strip-cells"] button span[aria-hidden]'),
    ) as HTMLElement[];

    // no_hist (z null, score null) → floor; normal (z 0.4 → 0.08, score 0.5/2
    // = 0.25 → height encodes 0.25); attack (max(1.2, 4) capped at 1)
    const [hist, normal, attack] = cells;
    expect(hist.style.height).toBe(`${CELL_MARGIN}px`);
    expect(Number.parseFloat(attack.style.height)).toBeGreaterThan(
      Number.parseFloat(normal.style.height),
    );
    expect(Number.parseFloat(normal.style.height)).toBeGreaterThan(CELL_MARGIN);
    expect(Number.parseFloat(attack.style.height)).toBeCloseTo(48, 0);
  });

  it("cursor snaps to the selected hour (aria-pressed + glow), click selects it", async () => {
    const user = userEvent.setup();
    const onSelectHour = vi.fn();
    const { container } = renderStrip({ selectedHourIso: T2, onSelectHour });

    const cells = container.querySelectorAll('[data-testid="strip-cells"] button');
    const [hist, normal, attack] = Array.from(cells) as HTMLElement[];
    expect(normal.getAttribute("aria-pressed")).toBe("true");
    expect(hist.getAttribute("aria-pressed")).toBe("false");
    // the cursor glows in the hour's verdict color and the outline marks it
    expect(normal.getAttribute("style")).toContain("outline");
    expect(normal.innerHTML).toContain("box-shadow");

    await user.click(attack);
    expect(onSelectHour).toHaveBeenCalledWith(T3);
  });

  it("shows the station switcher when the run holds several stations", async () => {
    const user = userEvent.setup();
    const onStationChange = vi.fn();
    renderStrip({
      stations: ["AZ_Tucson_11_W", "CA_Cupertino_1_E"],
      selectedHourIso: null,
      onStationChange,
    });

    const switcher = screen.getByTestId("strip-station-switcher");
    expect(switcher.textContent).toContain("CA_Cupertino_1_E");
    await user.click(screen.getByRole("button", { name: "CA_Cupertino_1_E" }));
    expect(onStationChange).toHaveBeenCalledWith("CA_Cupertino_1_E");
  });

  it("no switcher for a single-station run", () => {
    renderStrip();
    expect(screen.queryByTestId("strip-station-switcher")).toBeNull();
  });

  it("carries the muted axis-free sparkline", () => {
    renderStrip();
    expect(screen.getByTestId("strip-sparkline")).toBeTruthy();
  });

  it("loading state renders the skeleton, and empty data gets the quiet line", () => {
    const { rerender } = renderStrip({ series: null, loading: true });

    expect(document.querySelector(".animate-pulse")).toBeTruthy();

    rerender(
      <FDIEvidenceStrip
        series={makeSeries([])}
        loading={false}
        stations={["AZ_Tucson_11_W"]}
        station="AZ_Tucson_11_W"
        onStationChange={() => {}}
        selectedHourIso={null}
        onSelectHour={() => {}}
        threshold={1}
      />,
    );
    expect(screen.getByText(/No scored hours for AZ_Tucson_11_W yet/)).toBeTruthy();
  });
});
