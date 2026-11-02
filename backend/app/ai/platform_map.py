"""
What the assistant is allowed to point at.

Every place in the dashboard the copilot can send someone is listed here, and
nowhere else. The model never writes a URL: it picks an `id` from this table
and the backend resolves it into a route, a parameter and a DOM anchor. A
hallucinated id resolves to nothing and the answer is delivered without a jump,
which is the failure mode we want — a wrong sentence is recoverable, a
navigation to a route that does not exist is a broken app.

`anchor` is the `data-tour` attribute on the element the frontend highlights.
Adding a destination means adding a row here and putting the matching
`data-tour` on the component; nothing else changes.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Target:
    id: str
    label: str
    route: str
    anchor: str
    description: str


TARGETS: list[Target] = [
    Target(
        id="station-inspector",
        label="3D data inspector",
        route="/dashboard/fdi",
        anchor="station-3d",
        description=(
            "The 3D view on the FDI dashboard: three fixed ground nodes — the "
            "target station, a witness station and the weather input — whose "
            "labels show the readings of the hour selected on the timeline. The "
            "target's beacon flashes red while that hour is an attack. Drag to "
            "orbit, scroll to zoom."
        ),
    ),
    Target(
        id="fdi-control-center",
        label="FDI control center",
        route="/dashboard/fdi",
        anchor="fdi-control-center",
        description=(
            "The overlay on the 3D view: the data ingestion hub (drag-and-drop "
            "CSV, or load the demo dataset) and the live summary for the hour "
            "currently selected on the timeline. Turns red and flashes its "
            "beacon while that hour is classified as an attack."
        ),
    ),
    Target(
        id="fdi-timeline",
        label="Detection timeline",
        route="/dashboard/fdi",
        anchor="fdi-timeline",
        description=(
            "The anomaly-score timeline under the 3D view: one point per "
            "station-hour, colored by level (attack, suspect, normal). Clicking "
            "a point selects that hour and drives every explanation below it."
        ),
    ),
    Target(
        id="fdi-xai-shap",
        label="SHAP explanations",
        route="/dashboard/fdi",
        anchor="fdi-xai-shap",
        description=(
            "The SHAP tab: how each witness feature pushed the stage-1 "
            "LightGBM detector toward (red) or away from (blue) firing the "
            "alert for the selected hour. The decomposition is exact "
            "(TreeSHAP), so the bars add up to the stage-1 score minus the "
            "training baseline."
        ),
    ),
    Target(
        id="fdi-xai-temporal",
        label="Expected vs actual",
        route="/dashboard/fdi",
        anchor="fdi-xai-temporal",
        description=(
            "The temporal tab: the digital twin's expected curve against the "
            "values actually received, with the anomaly envelope around it. The "
            "gap between the two lines is the signal the detector scores."
        ),
    ),
    Target(
        id="sidebar",
        label="Navigation sidebar",
        route="/dashboard/fdi",
        anchor="app-sidebar",
        description=(
            "The left-hand navigation: the FDI Detection link, the theme "
            "toggle, and the account menu with its sign-out."
        ),
    ),
]

TARGETS_BY_ID = {target.id: target for target in TARGETS}


def catalogue() -> str:
    """The destination list as the model sees it."""

    return "\n".join(f"- {target.id}: {target.description}" for target in TARGETS)


def resolve(target_id: str | None) -> dict | None:
    """
    Turn what the model picked into something the frontend can navigate to.

    Returns None for anything unrecognised rather than raising: an answer with
    no jump is a fine outcome, and a made-up id should degrade to that quietly.
    """

    if not target_id:
        return None

    target = TARGETS_BY_ID.get(target_id.strip())
    if target is None:
        print(f"Assistant picked an unknown target id: {target_id!r} — ignoring.")
        return None

    return {
        "id": target.id,
        "label": target.label,
        "route": target.route,
        "params": {},
        "anchor": target.anchor,
    }