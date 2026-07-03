#include <metal_stdlib>
#include <SwiftUI/SwiftUI.h>
using namespace metal;

// Iridescent plasma sphere for the voice orb — domain-warped fbm noise flowed through a
// cosine palette, shaded as a glass ball (depth falloff + rim light + specular), fully
// contained in a circle with an anti-aliased edge. Uniforms: canvas size, time (already
// volume-warped on the CPU side), live level 0-1, and a palette phase per mode.

static float hash21(float2 p) {
    p = fract(p * float2(234.34, 435.345));
    p += dot(p, p + 34.23);
    return fract(p.x * p.y);
}

static float vnoise(float2 p) {
    float2 i = floor(p);
    float2 f = fract(p);
    float2 u = f * f * (3.0 - 2.0 * f);
    float a = hash21(i);
    float b = hash21(i + float2(1, 0));
    float c = hash21(i + float2(0, 1));
    float d = hash21(i + float2(1, 1));
    return mix(mix(a, b, u.x), mix(c, d, u.x), u.y);
}

static float fbm(float2 p) {
    float v = 0.0, amp = 0.5;
    for (int i = 0; i < 4; i++) {
        v += amp * vnoise(p);
        p = p * 2.03 + float2(17.3, 9.1);
        amp *= 0.5;
    }
    return v;
}

// IQ cosine palette tuned to Siri-ish blues/cyans/magentas with white-hot peaks.
static half3 palette(float t) {
    float3 a = float3(0.58, 0.55, 0.75);
    float3 b = float3(0.42, 0.45, 0.35);
    float3 c = float3(1.0, 1.0, 1.0);
    float3 d = float3(0.62, 0.34, 0.20);
    float3 col = a + b * cos(6.28318 * (c * t + d));
    return half3(col);
}

[[ stitchable ]] half4 voiceOrb(float2 position, half4 color,
                                float2 size, float time, float level, float phase) {
    float2 p = (position / size - 0.5) * 2.0;      // centered, -1..1
    float r = length(p);
    if (r > 1.0) { return half4(0.0); }

    float z = sqrt(max(0.0, 1.0 - r * r));          // fake sphere depth
    float t = time;

    // Domain-warped flow: two nested fbm lookups make the plasma fold and swirl
    // instead of scrolling; level tightens the swirl and speeds the churn.
    float2 q = p * (1.35 + 0.35 * level);
    float2 warp = float2(fbm(q + float2(t * 0.22, -t * 0.13)),
                         fbm(q + float2(-t * 0.17, t * 0.19) + 5.2));
    float v = fbm(q * 1.6 + 2.2 * warp + float2(0.0, t * 0.11));

    half3 col = palette(v * 1.15 + phase + 0.08 * sin(t * 0.3));

    // Second iridescent sheen layer, offset palette read — gives the oil-slick shimmer.
    float v2 = fbm(q * 2.6 - 1.7 * warp + float2(t * 0.07, 0.0));
    col = mix(col, palette(v2 + phase + 0.45), half(0.35));

    // Hot core that swells with voice level.
    float core = exp(-r * r * 5.5) * (0.45 + 0.85 * level);
    col += half3(core);

    // Sphere shading: depth falloff, cool rim light, top-left specular.
    col *= half(0.5 + 0.5 * z);
    float rim = pow(1.0 - z, 3.0);
    col += half3(0.45h, 0.55h, 0.9h) * half(rim * 0.8);
    float spec = pow(max(0.0, 1.0 - length(p - float2(-0.42, -0.46)) * 1.35), 3.0);
    col += half3(spec * 0.5);

    float alpha = smoothstep(1.0, 0.985, r);        // anti-aliased circular edge
    return half4(col * half(alpha), half(alpha));
}
