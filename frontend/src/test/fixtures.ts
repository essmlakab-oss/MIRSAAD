/**
 * §2.9 fixtures: one XAI hour payload shaped exactly like the frozen §1.7
 * contract (typed against the generated module), reuse across the panel,
 * metric-card and AI specs.
 */

import type { XaiHour } from "@/lib/fdi-api";

/**
 * The backend's ECOD quantile features, grouped by the v3 five-family map.
 * Nine features, not ten — the backend may drop `rain_missing24` on some
 * hours, and the panels must not assume a fixed length.
 */
const ECOD_FEATURES: XaiHour["ecod"]["features"] = [
  {
    key: "jump_ex",
    label: "Jump not explained by the 10 cm depth",
    family: "Paired-step fingerprint",
    value: 0.044,
    tail: "upper",
    p: 0.001,
    contribution: 6.9,
    signed_contribution: 6.9,
    rank: 1,
  },
  {
    key: "jz",
    label: "Step size in jump-magnitude units",
    family: "Paired-step fingerprint",
    value: 3.1,
    tail: "upper",
    p: 0.004,
    contribution: 3.1,
    signed_contribution: 3.1,
    rank: 2,
  },
  {
    key: "ratio_dev",
    label: "sm5/sm10 ratio deviation",
    family: "Depth-relationship",
    value: 1.04,
    tail: "upper",
    p: 0.05,
    contribution: 2.2,
    signed_contribution: 2.2,
    rank: 3,
  },
  {
    key: "ratio1020_dev",
    label: "sm10/sm20 ratio deviation",
    family: "Depth-relationship",
    value: 0.98,
    tail: "lower",
    p: 0.4,
    contribution: 1.4,
    signed_contribution: -1.4,
    rank: 4,
  },
  {
    key: "inc5_24",
    label: "5 cm moisture increment over 24h",
    family: "Unresolved increment",
    value: 0.051,
    tail: "upper",
    p: 0.03,
    contribution: 2.4,
    signed_contribution: 2.4,
    rank: 5,
  },
  {
    key: "rain_implaus24",
    label: "Moisture rise implausible vs 24h rain",
    family: "Rain plausibility",
    value: 1.8,
    tail: "upper",
    p: 0.08,
    contribution: 1.8,
    signed_contribution: 1.8,
    rank: 6,
  },
  {
    key: "rise_nosupport72",
    label: "Moisture rise with no 72h rain support",
    family: "Rain plausibility",
    value: 0.9,
    tail: "lower",
    p: 0.6,
    contribution: 0.9,
    signed_contribution: -0.9,
    rank: 7,
  },
  {
    key: "dup_later_m24",
    label: "Value replayed from at least 24h earlier",
    family: "Replay",
    value: 1,
    tail: "upper",
    p: 0.7,
    contribution: 0.6,
    signed_contribution: 0.6,
    rank: 8,
  },
];

/** Nine features across the five v3 families (one rain witness absent). */
const ECOD_FAMILIES: XaiHour["ecod"]["families"] = [
  {
    family: "Paired-step fingerprint",
    signed_contribution: 10.0,
    contribution_sum: 10.0,
    share_pct: 51.8,
  },
  {
    family: "Depth-relationship",
    signed_contribution: 0.8,
    contribution_sum: 3.6,
    share_pct: 18.7,
  },
  {
    family: "Unresolved increment",
    signed_contribution: 2.4,
    contribution_sum: 2.4,
    share_pct: 12.4,
  },
  {
    family: "Rain plausibility",
    signed_contribution: 0.9,
    contribution_sum: 2.7,
    share_pct: 14.0,
  },
  { family: "Replay", signed_contribution: 0.6, contribution_sum: 0.6, share_pct: 3.1 },
];

/** All 20 twin rows, ranked by |SHAP|. */
const TWIN_FEATURES: XaiHour["shap_twin"]["features"] = Array.from({ length: 20 }, (_, i) => ({
  key: `feature_${i}`,
  label: `Witness feature ${i + 1}`,
  value: 0.1 - i * 0.005,
  shap: ((10 - i) / 100) * (i % 2 === 0 ? 1 : -1),
  contribution_sign_ok: true,
  rank: i + 1,
})).map((row, index) => ({
  ...row,
  key: index === 0 ? "sm10" : index === 1 ? "rain" : row.key,
  label: index === 0 ? "10cm Moisture" : index === 1 ? "Rain" : row.label,
}));

export const HOUR_ISO = "2025-03-29T00:00:00Z";

function basePayload(niveau: "ATTAQUE" | "SUSPECT" | "normal"): XaiHour {
  const alert = niveau !== "normal";
  return {
    meta: {
      station: "AZ_Tucson_11_W",
      hour_iso: HOUR_ISO,
      niveau,
      score: niveau === "ATTAQUE" ? 0.93 : 0.71,
      alert,
      model_version: "v3+stage1-stage2",
      feature_version: "witness-v3+ecod-v1",
      threshold: 0.431,
      ground_truth: "attack",
    },
    summary: {
      expected_sm5: 0.115,
      observed_sm5: 0.575,
      deviation_pct: 400,
      twin_residual_z: 12.2,
      verdict: alert ? "ALERT" : "NORMAL",
    },
    rules: {
      fired: "aucune",
      reason_text: null,
      details: {},
      recommended_action: "Investigate — suspect, often a drift the raw rules miss.",
    },
    ecod: {
      score: 3.4,
      threshold: 2.0,
      suspect: true,
      features: ECOD_FEATURES,
      families: ECOD_FAMILIES,
    },
    shap_twin: {
      target: "twin_sm5",
      base_value: 0.12,
      features: TWIN_FEATURES,
    },
    temporal: {
      window_hours: 24,
      points: Array.from({ length: 25 }, (_, i) => ({
        t: new Date(new Date(HOUR_ISO).getTime() + (i - 12) * 3_600_000).toISOString(),
        expected: 0.115 + i * 0.001,
        observed: 0.575 + i * 0.001,
      })),
    },
  };
}

/** An ATTACK hour where rule marches fired — the full rule-trace state. */
export const marchHour: XaiHour = {
  ...basePayload("ATTAQUE"),
  meta: {
    ...basePayload("ATTAQUE").meta,
    score: 0.97,
    ground_truth: "attack",
  },
  rules: {
    fired: "marches",
    reason_text:
      "v3 flagged this hour as paired steps on the 5 cm probe — the deeper witnesses do not follow.",
    details: {
      step_up: 0.0443,
      step_down: -0.0423,
      gap_hours: 61,
      unexplained_10cm: true,
      offset_vs_twin: 0.0407,
    },
    recommended_action: "Reject / quarantine the reading — confirmed attack.",
  },
};

/** A SUSPECT hour where no rule fired — the ECOD-only state. */
export const ecodHour: XaiHour = {
  ...basePayload("SUSPECT"),
  summary: {
    ...basePayload("SUSPECT").summary,
    twin_residual_z: 1.2,
  },
};

/** A NORMAL hour — for the Normal badge. */
export const normalHour: XaiHour = {
  ...basePayload("normal"),
  summary: {
    ...basePayload("normal").summary,
    deviation_pct: 2.1,
    twin_residual_z: 0.4,
    verdict: "NORMAL",
  },
  meta: { ...basePayload("normal").meta, ground_truth: null, score: 0.6 },
};
