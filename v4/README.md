# PriorIA V4.1

V4 keeps the Python V2 clinical/event engine and replaces the Streamlit view
with a standalone browser scene served by FastAPI. Playback, time scaling,
scrubbing, and rendering stay in the browser's Canvas loop; they do not cause
Python or Streamlit reruns.

V4.1 preserves each patient's randomly selected chair for the whole waiting
spell. When a chair is released, only the next standing patient is assigned a
new random free chair. Animation transitions are staggered within each minute
so simultaneous event-clock calls do not appear as a single synchronized
movement. PriorIA prediction validation does not move a triage nurse into the
cab. The dashboard shows hourly arrivals, triage category counts, abandonment,
stage waits, and average resource utilization.

The floorplan is stored as a high-quality JPEG to keep the deployable artifact
compact without changing its dimensions.

## Run with Docker Compose

From the `v4` folder:

```bash
docker compose up --build
```

Open `http://localhost:8503`. To choose another host port, set
`PRIORIA_V4_PORT` before starting Compose.

## Scenario behavior

- The reference cohort is fixed at 100 synthetic patients per day. The demand
  multiplier scales that rate while preserving the configured hourly curve.
- The measured simulation horizon is set in whole days (1–7).
- A configurable warm-up of 0–3 whole days runs first. Patients arriving in
  warm-up are excluded from patient-level performance metrics, but unfinished
  patients and busy resources carry into the measured period.
- The initial warm-up is one day. Compare 1, 2, and 3 days over multiple seeds
  before treating it as adequate; inspect queue occupancy and waiting outcomes
  by time of day for stabilization.
- Attention nurses are configurable from 1 to 3. Physician totals remain
  limited to the 8 active consultation boxes.
- Playback speeds are x1, x2, x5, x10, x30, and x60. A new run starts playback
  automatically, and the browser controls allow pause, resume, reset, and
  timeline scrubbing.

The hourly arrival curve and clinical distributions are synthetic scenario
assumptions; they are not yet calibrated to DEIS. Warm-up excludes initialization
from reported outcomes but does not itself establish that the system is at a
steady or periodic operating state.

The dashboard defines categorization time as arrival to triage completion and
attention wait as triage completion to the first consultation call. Egress and
hospitalization time are measured from the last completed care stage to the
terminal disposition; these can be zero because no separate exit or transfer
duration is currently modeled. Chair utilization is based on occupied
chair-minutes; staff, cabin, and box utilization use the event trace clipped to
the measured window.
