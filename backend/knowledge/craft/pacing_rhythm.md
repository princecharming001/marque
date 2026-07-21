# Craft — Pacing & Rhythm (measured, not felt)

Shot lengths must VARY and CLUSTER. Cutting et al. 2010 (Psychological
Science, 150 films): successful modern editing shows (a) neighboring shot
lengths correlated — runs of short shots, runs of long — and (b) a 1/f
duration spectrum matching human attention fluctuation. Dancyger: "shots
should never be all the same length." Metronomic cadence is an amateur tell
(our jitter pass exists for exactly this; the lint verifies the outcome).

Tension needs release. Pearlman (Cutting Rhythms): rhythm is cycles of
tension and release — sustained intensity fatigues. After any fast-cut or
effect-dense cluster, the next beat must BREATHE: at least one hold ~1.5-2x
the local mean before the next build (the temporal form of GoE #23's
"wide shot after close-ups").

Reference bands: feature ASL fell 12s (1930) to ~2.5-4s (modern; action ~4s,
sub-2s in peaks — Salt/Cinemetrics, Follows). Short-form vertical has NO
peer-reviewed ASL canon — the only documented pacing numbers are hook
windows: TikTok-published 63% of high-CTR videos hook inside 3s; YouTube's
playbook makes the first 15s decisive. Treat "visible change every ~1.5-4s"
as practitioner convention (our interrupt cadences sit inside it), not law.

Enter late, exit early (Dmytryk 5): no shot opens or closes on dead time —
except a deliberate release beat, which is the ONE place air is craft.

```yaml
rules:
  - id: pace.varied_clustered
    principle: "Shot-length spread with local clustering (lag-1 autocorrelation > 0), long-tail distribution"
    source: "Cutting, DeLong & Nothelfer 2010; Dancyger"
    enforce: lint
    params: {min_gap_stddev_frames: 8}
  - id: pace.breathe_after_peak
    principle: "After an event-dense cluster, hold >= ~1.5x local mean gap before the next build"
    source: "Pearlman, Cutting Rhythms; GoE #23"
    enforce: lint
    params: {cluster_events: 3, cluster_window_f: 90, release_window_f: 240, release_gap_mult: 1.5}
  - id: pace.hook_window
    principle: "The promise (spoken, text, or visual) lands by 3.0s; no greeting block before it"
    source: "TikTok for Business (63% stat); YouTube Creator Playbook"
    enforce: prompt
```
