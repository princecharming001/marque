# Music catalog runbook

Replace the SoundHelix placeholder beds (`_BUILTIN_MUSIC_TRACKS` in
`backend/main.py`) with real, licensed tracks — beat grids included — with
zero code change.

## 1. Get tracks (by hand — Pixabay has NO API)

Go to [pixabay.com/music](https://pixabay.com/music/) and filter by mood.
Pixabay's Content License is commercial-OK with no attribution required, so
tracks are safe as rendered-video beds. Download **~8–15 tracks** spread
across **lo-fi / minimal / upbeat / cinematic** — these sit well under
talking-head content — preferring **BPM 80–120** (faster beds fight speech
cadence). Save them all into one folder, e.g. `~/Downloads/pixabay-music/`.

Keep at least 2 tracks per tone bucket (`calm` / `confident` / `energetic`)
so `_select_music_track` always has variety.

## 2. Describe them (optional but recommended)

Drop a `catalog_meta.json` next to the audio files. Anything you omit is
guessed from the filename. **Keep each track's Pixabay page URL in
`source_url`** — that is your licence provenance if a claim ever comes in.

```json
{
  "good-night-lofi.mp3": {
    "id": "good-night-lofi",
    "title": "Good Night",
    "vibe": "chill",
    "tone": "calm",
    "energy": "low",
    "license_note": "Pixabay Content License — commercial OK, no attribution",
    "source_url": "https://pixabay.com/music/beats-good-night-lofi-160166/"
  }
}
```

Allowed tag values (what the backend matches on):
`vibe`: driving | steady | chill | upbeat · `tone`: calm | confident |
energetic · `energy`: low | medium | high.

## 3. Run the script

Needs `pip install librosa soundfile` (the script tells you if missing).

Dry run first — analyzes beat grids and writes the catalog, no upload:

```bash
cd backend/scripts
python3 build_music_catalog.py ~/Downloads/pixabay-music --dry-run
```

Real run — uploads each file to Supabase Storage at `music/{id}.mp3` in the
public `marque-clips` bucket and writes real public URLs:

```bash
SUPABASE_URL="https://<project>.supabase.co" \
SUPABASE_SERVICE_KEY="<service role key>" \
python3 build_music_catalog.py ~/Downloads/pixabay-music
```

Per-file failures (corrupt download, odd codec) are skipped with a message —
the batch never aborts. Re-running is safe: uploads upsert, and the catalog
merge-updates by `id`, so you can add tracks a few at a time.

## 4. Ship it

The script ends by printing the compact one-line JSON. On Render
(marque backend service → Environment), set:

```
MUSIC_CATALOG = <the compact JSON — entire contents of music_catalog.json on one line>
```

then restart the service. `_load_music_catalog` picks it up on boot;
`/v1/music` immediately serves the new tracks to iOS. No deploy needed.

## Notes

- Each track dict carries the backend schema (`name`, `url`, `vibe`, `tone`,
  `bpm`, `energy`) **plus** `beat_grid` (beat times, seconds, 3 dp) and
  `beat_conf` (0–1). **A grid with `beat_conf < 0.5` must not be snapped
  to** — the tracker guessed it on weak evidence (ambient pads, etc.);
  fall back to plain cuts.
- Extra keys (`id`, `title`, `license_note`, `source_url`, `beat_grid`) are
  ignored harmlessly by the backend today (`_load_music_catalog` only checks
  `url`) and are there for future beat-snap consumers + licence provenance.
- Licence: Pixabay Content License (no attribution, commercial use OK). Do
  NOT redistribute the raw files outside the product; keep `source_url`
  filled in `catalog_meta.json` for every track.
