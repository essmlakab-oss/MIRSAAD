# 3D model

`antenna.glb` — the ground antenna at the heart of the Data Inspector scene
(`src/components/station-scene.tsx`). Normalised at runtime to a fixed height
and re-aimed every time a new hour is selected, so its internal units and
facing do not matter.

**Source not recorded.** The file predates this repository's current scope and
its provenance (author, licence) was never written down. Replace it with a
clearly licensed model — CC0/CC-BY, downloadable without authentication — if
this scene ever ships anywhere public, and note the attribution here.

## What happened to the other models

Earlier revisions of this page rendered a per-sensor GLB for each node
(`sensor-humidity.glb`, `sensor-pressure.glb`, …) fetched from Sketchfab. That
telemetry-node view has been removed; the Data Inspector now draws exactly
three fixed ground nodes from primitives, and the only remaining GLB is the
antenna above.
