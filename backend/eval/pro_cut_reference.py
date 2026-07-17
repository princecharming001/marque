"""PRO-CUT REFERENCE (2026-07-17) — ground truth from a real professional editor.

The owner supplied a raw 50s talking-head take (140 words, three false-started hook
attempts + one mid-sentence restart) AND a professional editor's cut of the same take.
Aligning them word-by-word produced this measured grammar:

  - CONTENT cuts: the false-started takes ONLY (~6.9s). Everything else kept verbatim
    — including the full CTA ("Rate that version 7 out of 10. Follow for the next
    collision test."), which Marque's plan LLM once nuked as a "false_start".
  - PAUSES: of 22 natural pauses >=300ms, ~17 preserved to the millisecond
    (385->385, 481->481, 368->369...). Overall silence ratio 32% -> 30%. Only the two
    longest stalls (577ms, 690ms) were tightened. Marque's old policy (cut >350ms down
    to 200ms) made NINETEEN dead-air micro-splices on this take — the "choppy" sound.
  - SPLICES: exactly one mid-video splice, at a sentence boundary.

The fixture below is the REAL raw take's word timings. The tests in
test_pro_cut_reference.py enforce the measured grammar on the pipeline forever:
pause preservation, seam budget, ending retention, false-start removal.
"""
from __future__ import annotations

RAW_WORDS: list[dict] = [
{
"word": "Most",
"start_ms": 1009,
"end_ms": 1266
},
{
"word": "fusion\u2014",
"start_ms": 1378,
"end_ms": 3238
},
{
"word": "most",
"start_ms": 3462,
"end_ms": 3671
},
{
"word": "fusion\u2014",
"start_ms": 3719,
"end_ms": 4007
},
{
"word": "most",
"start_ms": 5145,
"end_ms": 5258
},
{
"word": "fusion",
"start_ms": 5258,
"end_ms": 5578
},
{
"word": "fails",
"start_ms": 5626,
"end_ms": 5931
},
{
"word": "for",
"start_ms": 6027,
"end_ms": 6123
},
{
"word": "the",
"start_ms": 6123,
"end_ms": 6203
},
{
"word": "same",
"start_ms": 6203,
"end_ms": 6444
},
{
"word": "reason.",
"start_ms": 6444,
"end_ms": 6716
},
{
"word": "You're",
"start_ms": 7069,
"end_ms": 7165
},
{
"word": "not",
"start_ms": 7165,
"end_ms": 7358
},
{
"word": "matching\u2014",
"start_ms": 7406,
"end_ms": 7694
},
{
"word": "you're\u2014",
"start_ms": 8207,
"end_ms": 8800
},
{
"word": "most",
"start_ms": 8848,
"end_ms": 9009
},
{
"word": "fusion",
"start_ms": 9009,
"end_ms": 9329
},
{
"word": "fails",
"start_ms": 9329,
"end_ms": 9618
},
{
"word": "for",
"start_ms": 9650,
"end_ms": 9746
},
{
"word": "the",
"start_ms": 9746,
"end_ms": 9810
},
{
"word": "same",
"start_ms": 9810,
"end_ms": 10051
},
{
"word": "reason.",
"start_ms": 10051,
"end_ms": 10259
},
{
"word": "You're",
"start_ms": 10596,
"end_ms": 10676
},
{
"word": "matching",
"start_ms": 10676,
"end_ms": 10981
},
{
"word": "flavors",
"start_ms": 10997,
"end_ms": 11333
},
{
"word": "instead",
"start_ms": 11333,
"end_ms": 11558
},
{
"word": "of",
"start_ms": 11558,
"end_ms": 11702
},
{
"word": "fat,",
"start_ms": 11814,
"end_ms": 12103
},
{
"word": "acid,",
"start_ms": 12279,
"end_ms": 12600
},
{
"word": "and",
"start_ms": 12632,
"end_ms": 12744
},
{
"word": "heat.",
"start_ms": 12760,
"end_ms": 12872
},
{
"word": "Everyone",
"start_ms": 13257,
"end_ms": 13497
},
{
"word": "tries",
"start_ms": 13497,
"end_ms": 13770
},
{
"word": "to",
"start_ms": 13770,
"end_ms": 13866
},
{
"word": "pair",
"start_ms": 13882,
"end_ms": 14123
},
{
"word": "fusion",
"start_ms": 14123,
"end_ms": 14363
},
{
"word": "by",
"start_ms": 14844,
"end_ms": 14940
},
{
"word": "taste.",
"start_ms": 14940,
"end_ms": 15277
},
{
"word": "Gochujang",
"start_ms": 15645,
"end_ms": 16351
},
{
"word": "tastes",
"start_ms": 16703,
"end_ms": 16912
},
{
"word": "bold,",
"start_ms": 17008,
"end_ms": 17345
},
{
"word": "carbonara",
"start_ms": 17569,
"end_ms": 18066
},
{
"word": "tastes",
"start_ms": 18066,
"end_ms": 18371
},
{
"word": "rich.",
"start_ms": 18371,
"end_ms": 18595
},
{
"word": "Throw",
"start_ms": 18948,
"end_ms": 19092
},
{
"word": "them",
"start_ms": 19092,
"end_ms": 19188
},
{
"word": "together,",
"start_ms": 19188,
"end_ms": 19557
},
{
"word": "done.",
"start_ms": 19829,
"end_ms": 19958
},
{
"word": "That's",
"start_ms": 20535,
"end_ms": 20711
},
{
"word": "why",
"start_ms": 20711,
"end_ms": 20807
},
{
"word": "it",
"start_ms": 20807,
"end_ms": 20871
},
{
"word": "collapses.",
"start_ms": 20871,
"end_ms": 21368
},
{
"word": "Flavor",
"start_ms": 21737,
"end_ms": 22058
},
{
"word": "isn't",
"start_ms": 22138,
"end_ms": 22362
},
{
"word": "the",
"start_ms": 22394,
"end_ms": 22475
},
{
"word": "structure.",
"start_ms": 22475,
"end_ms": 22891
},
{
"word": "Fat,",
"start_ms": 23180,
"end_ms": 23388
},
{
"word": "acid,",
"start_ms": 23821,
"end_ms": 24142
},
{
"word": "and",
"start_ms": 24158,
"end_ms": 24238
},
{
"word": "heat",
"start_ms": 24238,
"end_ms": 24478
},
{
"word": "are.",
"start_ms": 24478,
"end_ms": 24639
},
{
"word": "Carbonara",
"start_ms": 24863,
"end_ms": 25440
},
{
"word": "is",
"start_ms": 25440,
"end_ms": 25536
},
{
"word": "fat.",
"start_ms": 25681,
"end_ms": 25873
},
{
"word": "And",
"start_ms": 26162,
"end_ms": 26226
},
{
"word": "salt",
"start_ms": 26242,
"end_ms": 26675
},
{
"word": "with",
"start_ms": 26804,
"end_ms": 26948
},
{
"word": "almost",
"start_ms": 27028,
"end_ms": 27269
},
{
"word": "no",
"start_ms": 27269,
"end_ms": 27365
},
{
"word": "acid.",
"start_ms": 27365,
"end_ms": 27654
},
{
"word": "Gochujang",
"start_ms": 28072,
"end_ms": 28537
},
{
"word": "brings",
"start_ms": 28714,
"end_ms": 29019
},
{
"word": "heat",
"start_ms": 29388,
"end_ms": 29693
},
{
"word": "and",
"start_ms": 29693,
"end_ms": 29853
},
{
"word": "funk,",
"start_ms": 29853,
"end_ms": 30158
},
{
"word": "but",
"start_ms": 30174,
"end_ms": 30319
},
{
"word": "also",
"start_ms": 30319,
"end_ms": 30527
},
{
"word": "sugar.",
"start_ms": 30560,
"end_ms": 30816
},
{
"word": "Smash",
"start_ms": 31153,
"end_ms": 31507
},
{
"word": "them",
"start_ms": 31539,
"end_ms": 31667
},
{
"word": "raw",
"start_ms": 32020,
"end_ms": 32133
},
{
"word": "and",
"start_ms": 32486,
"end_ms": 32630
},
{
"word": "the",
"start_ms": 32726,
"end_ms": 32807
},
{
"word": "sugar",
"start_ms": 32807,
"end_ms": 33208
},
{
"word": "fights",
"start_ms": 33208,
"end_ms": 33529
},
{
"word": "the",
"start_ms": 33529,
"end_ms": 33609
},
{
"word": "egg.",
"start_ms": 33609,
"end_ms": 33738
},
{
"word": "The",
"start_ms": 34091,
"end_ms": 34123
},
{
"word": "heat",
"start_ms": 34123,
"end_ms": 34476
},
{
"word": "flattens",
"start_ms": 34492,
"end_ms": 34829
},
{
"word": "the",
"start_ms": 34829,
"end_ms": 34990
},
{
"word": "pork.",
"start_ms": 34990,
"end_ms": 35150
},
{
"word": "So",
"start_ms": 35487,
"end_ms": 35792
},
{
"word": "I",
"start_ms": 35856,
"end_ms": 35905
},
{
"word": "match",
"start_ms": 36113,
"end_ms": 36322
},
{
"word": "the",
"start_ms": 36434,
"end_ms": 36579
},
{
"word": "axes",
"start_ms": 36659,
"end_ms": 36932
},
{
"word": "first.",
"start_ms": 37060,
"end_ms": 37413
},
{
"word": "Cut",
"start_ms": 37783,
"end_ms": 38039
},
{
"word": "the",
"start_ms": 38039,
"end_ms": 38120
},
{
"word": "gochujang",
"start_ms": 38200,
"end_ms": 38730
},
{
"word": "with",
"start_ms": 38762,
"end_ms": 38922
},
{
"word": "a",
"start_ms": 38922,
"end_ms": 38938
},
{
"word": "splash",
"start_ms": 38938,
"end_ms": 39388
},
{
"word": "of",
"start_ms": 39484,
"end_ms": 39564
},
{
"word": "vinegar",
"start_ms": 39564,
"end_ms": 39901
},
{
"word": "to",
"start_ms": 40222,
"end_ms": 40383
},
{
"word": "give",
"start_ms": 40527,
"end_ms": 40688
},
{
"word": "the",
"start_ms": 40688,
"end_ms": 40832
},
{
"word": "fat",
"start_ms": 40832,
"end_ms": 40961
},
{
"word": "something",
"start_ms": 41009,
"end_ms": 41330
},
{
"word": "to",
"start_ms": 41330,
"end_ms": 41394
},
{
"word": "lean",
"start_ms": 41410,
"end_ms": 41587
},
{
"word": "on.",
"start_ms": 41587,
"end_ms": 41795
},
{
"word": "Pull",
"start_ms": 42132,
"end_ms": 42245
},
{
"word": "the",
"start_ms": 42277,
"end_ms": 42437
},
{
"word": "heat",
"start_ms": 42437,
"end_ms": 42614
},
{
"word": "back",
"start_ms": 42614,
"end_ms": 42919
},
{
"word": "so",
"start_ms": 42919,
"end_ms": 43176
},
{
"word": "it",
"start_ms": 43176,
"end_ms": 43256
},
{
"word": "seasons",
"start_ms": 43256,
"end_ms": 43657
},
{
"word": "instead",
"start_ms": 43657,
"end_ms": 43898
},
{
"word": "of",
"start_ms": 43898,
"end_ms": 43930
},
{
"word": "screams.",
"start_ms": 43930,
"end_ms": 44347
},
{
"word": "Then",
"start_ms": 44684,
"end_ms": 44797
},
{
"word": "it",
"start_ms": 44797,
"end_ms": 45102
},
{
"word": "works.",
"start_ms": 45102,
"end_ms": 45423
},
{
"word": "Rate",
"start_ms": 45808,
"end_ms": 45969
},
{
"word": "that",
"start_ms": 45969,
"end_ms": 46097
},
{
"word": "version",
"start_ms": 46145,
"end_ms": 46434
},
{
"word": "7",
"start_ms": 47124,
"end_ms": 47734
},
{
"word": "out",
"start_ms": 47750,
"end_ms": 47991
},
{
"word": "of",
"start_ms": 47991,
"end_ms": 48023
},
{
"word": "10.",
"start_ms": 48023,
"end_ms": 48087
},
{
"word": "Follow",
"start_ms": 48553,
"end_ms": 48874
},
{
"word": "for",
"start_ms": 48874,
"end_ms": 48986
},
{
"word": "the",
"start_ms": 48986,
"end_ms": 49034
},
{
"word": "next",
"start_ms": 49098,
"end_ms": 49323
},
{
"word": "collision",
"start_ms": 49419,
"end_ms": 49676
},
{
"word": "test.",
"start_ms": 49676,
"end_ms": 50013
}
]

# Raw-timeline spans (ms) the professional editor REMOVED (false starts + restart).
PRO_CUT_SPANS_MS: list[list[int]] = [[1009, 4007], [7069, 10676], [36659, 36932]]

# Natural pauses (>=300ms, same-take adjacent words) and what the pro left of them.
PRO_PAUSE_BAND_MS = (300, 550)   # pauses in this band were preserved essentially verbatim
PRO_PAUSE_MIN_KEPT_MS = 250      # ...never compressed below a real beat


def natural_pauses(words: list[dict], lo: int = 300, hi: int = 550) -> list[tuple[int, int, int]]:
    """(prev_end_ms, next_start_ms, gap_ms) for every natural inter-word pause in [lo, hi]."""
    out = []
    for a, b in zip(words, words[1:]):
        gap = b["start_ms"] - a["end_ms"]
        if lo <= gap <= hi:
            out.append((a["end_ms"], b["start_ms"], gap))
    return out


def pause_survival(drops: list, words: list[dict],
                   lo: int = 300, hi: int = 550, min_kept_ms: int = 250) -> tuple[int, int]:
    """(survived, total): natural pauses in [lo,hi] whose residual (after dead-air drops)
    is still >= min_kept_ms. The pro preserved essentially all of them."""
    spans = [(d.src_in if hasattr(d, "src_in") else d["src_in"],
              d.src_out if hasattr(d, "src_out") else d["src_out"]) for d in drops]
    survived = total = 0
    for end_ms, start_ms, gap in natural_pauses(words, lo, hi):
        total += 1
        cut = sum(max(0, (min(b * 100 // 3, start_ms) - max(a * 100 // 3, end_ms)))
                  for a, b in spans)
        if gap - cut >= min_kept_ms:
            survived += 1
    return survived, total
