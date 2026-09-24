import Foundation

// LV-1: a storage SIZE refusal (Supabase: HTTP 400 + {"statusCode":"413"} for any object over
// its 50 MiB project limit, whatever the mint advertised) must recompress + retry, never
// fail permanently — and only a body over the limit can be a size refusal.
func runUploadRetryPolicyTests() {
    suite("UploadRetryPolicy — LV-1 storage size refusal")
    let mib50: Int64 = 52_428_800

    // The prod repro: a 91.5 MB raw take PUT against the lying 150 MB cap → 400 (413 body).
    expectEqual(UploadRetryPolicy.decide(status: 400, attempt: 0, bodyBytes: 91_500_000,
                                         capBytes: 150_000_000),
                .recompressSmaller(targetBytes: 50_000_000),
                "400 on a 91.5MB body vs 150MB cap → recompress to 50,000,000")
    expectEqual(UploadRetryPolicy.decide(status: 413, attempt: 0, bodyBytes: 60_000_000,
                                         capBytes: 48_000_000),
                .recompressSmaller(targetBytes: 48_000_000),
                "real 413 on a 60MB body vs 48MB cap → recompress to the (smaller) cap")
    expectEqual(UploadRetryPolicy.decide(status: 400, attempt: 0, bodyBytes: mib50 + 1,
                                         capBytes: 150_000_000),
                .recompressSmaller(targetBytes: 50_000_000),
                "400 one byte over 50 MiB → size refusal")

    // A 400 on a body the store WOULD accept is not about size: stays fail-fast.
    expectEqual(UploadRetryPolicy.decide(status: 400, attempt: 0, bodyBytes: mib50,
                                         capBytes: 150_000_000),
                .fail, "400 at exactly 50 MiB (accepted size) → fail")
    expectEqual(UploadRetryPolicy.decide(status: 400, attempt: 0, bodyBytes: 40_000_000,
                                         capBytes: 150_000_000),
                .fail, "400 on a 40MB body → fail (token/other, not size)")
    expectEqual(UploadRetryPolicy.decide(status: 400, attempt: 0), .fail,
                "400 with unknown body size (0) → fail")

    // Bounded: one size recompression per session, and never after the budget is spent.
    expectEqual(UploadRetryPolicy.decide(status: 400, attempt: 1, bodyBytes: 91_500_000,
                                         capBytes: 150_000_000, sizeRecompressions: 1),
                .fail, "second size refusal in a session → fail")
    expectEqual(UploadRetryPolicy.decide(status: 400, attempt: 5, bodyBytes: 91_500_000,
                                         capBytes: 150_000_000),
                .fail, "session attempts exhausted → fail even for a size refusal")
    expectEqual(UploadRetryPolicy.decide(status: 400, attempt: 0, lifetimeAttempt: 9,
                                         bodyBytes: 91_500_000, capBytes: 150_000_000),
                .fail, "lifetime attempts exhausted → fail even for a size refusal")

    // Other statuses keep their classification when the body is big.
    expectEqual(UploadRetryPolicy.decide(status: 404, attempt: 0, bodyBytes: 91_500_000,
                                         capBytes: 150_000_000),
                .fail, "404 on a big body stays fail-fast")
    expectEqual(UploadRetryPolicy.decide(status: 403, attempt: 0, bodyBytes: 91_500_000,
                                         capBytes: 150_000_000),
                .remintThenRetry, "403 on a big body stays re-mint")
    expectEqual(UploadRetryPolicy.decide(status: 409, attempt: 0, bodyBytes: 91_500_000,
                                         capBytes: 150_000_000),
                .fail, "409 on a big body stays fail-fast")

    suite("UploadRetryPolicy — LV-1 recompress target")
    expectEqual(UploadRetryPolicy.recompressTarget(capBytes: 150_000_000, refusedBodyBytes: 91_500_000),
                50_000_000, "target = min(cap, 50,000,000)")
    expectEqual(UploadRetryPolicy.recompressTarget(capBytes: 0, refusedBodyBytes: 60_000_000),
                50_000_000, "unknown cap → 50,000,000")
    expectEqual(UploadRetryPolicy.recompressTarget(capBytes: 30_000_000, refusedBodyBytes: 60_000_000),
                30_000_000, "an honest smaller cap wins")
    expectEqual(UploadRetryPolicy.recompressTarget(capBytes: 150_000_000, refusedBodyBytes: 49_000_000),
                nil, "no target when it wouldn't be smaller than the refused body")
    expect(UploadRetryPolicy.storageRefusedSize(status: 400, bodyBytes: 110_000_000),
           "the 2026-08-18 prod failure (110MB, 400) is a size refusal")
    expect(!UploadRetryPolicy.storageRefusedSize(status: 500, bodyBytes: 110_000_000),
           "a 500 is never a size refusal")

    suite("UploadRetryPolicy — LV-1 effective cap (learned storage limit)")
    let now = 1_800_000_000.0
    expectEqual(UploadRetryPolicy.effectiveCap(mintCap: 150_000_000, lastSizeRefusalEpoch: nil, now: now),
                150_000_000, "no refusal seen → trust the mint cap")
    expectEqual(UploadRetryPolicy.effectiveCap(mintCap: 150_000_000, lastSizeRefusalEpoch: now - 3600, now: now),
                50_000_000, "refusal 1h ago → clamp the lying cap to 50,000,000")
    expectEqual(UploadRetryPolicy.effectiveCap(mintCap: 150_000_000,
                                               lastSizeRefusalEpoch: now - StorageSizeMemory.ttl - 1, now: now),
                150_000_000, "refusal older than the TTL → mint cap again (a raised limit comes back)")
    expectEqual(UploadRetryPolicy.effectiveCap(mintCap: 40_000_000, lastSizeRefusalEpoch: now - 60, now: now),
                40_000_000, "an honest cap under the limit is never raised")
    expectEqual(UploadRetryPolicy.effectiveCap(mintCap: nil, lastSizeRefusalEpoch: nil, now: now),
                48_000_000, "mint omitted the cap → 48,000,000 default")
    expectEqual(UploadRetryPolicy.effectiveCap(mintCap: 0, lastSizeRefusalEpoch: nil, now: now),
                48_000_000, "mint cap 0 → default")
    expectEqual(UploadRetryPolicy.effectiveCap(mintCap: 150_000_000, lastSizeRefusalEpoch: now + 3600, now: now),
                150_000_000, "a refusal stamp in the future (clock change) is ignored")
}
