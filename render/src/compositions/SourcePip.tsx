import React from "react";
import { AbsoluteFill, Sequence, OffthreadVideo, Img, Freeze, useCurrentFrame, interpolate } from "remotion";
import { CutVideo } from "../components/CutVideo";
import { AudioMix } from "../components/AudioMix";
import { TextStickers } from "../components/TextStickers";
import { BrollLayer } from "../components/BrollLayer";
import { Grade } from "../components/Grade";
import { Captions } from "../components/Captions";
import { ProgressBar } from "../components/ProgressBar";
import { EndCard } from "../components/EndCard";
import { CompositionProps, ReactWindow } from "../types";
import { LAYOUT } from "../layout";

// Addendum mode E — SOURCE-PRIMARY + SPEAKER PIP. The reacted-to/source media fills the
// frame (driven by the same play/freeze/duck schedule as DuetSplit); the speaker rides in
// a PIP (circle or rounded rect per layout.speaker_treatment, positioned per
// layout.pip_position). The punch-in system is OFF inside the PIP — it's too small to
// read, and a zoom there looks like a glitch. Captions render at their normal band
// (~62%), well clear of the bottom-anchored PIP. Falls back to a full-frame talking head
// when no source is attached, exactly like DuetSplit's fallback.
const PIP_CIRCLE_DIA = 300;
const PIP_RECT_W = 340;                 // ~31% frame width (spec: 28-33%)
const PIP_RECT_H = 500;                 // speaker is 9:16 → keep a portrait-ish window
const PIP_MARGIN_X = 40;
const PIP_MARGIN_BOTTOM = 340;          // clear of the platform bottom UI (safe zone 320)

export const SourcePip: React.FC<CompositionProps> = ({ sourceUrl, edl }) => {
  const react = edl?.react_source;

  if (!react?.resolved_url) {
    // No source clip — degrade to a plain talking-head cut so it still renders.
    return (
      <AbsoluteFill style={{ background: "#000" }}>
        <CutVideo sourceUrl={sourceUrl} clips={edl?.clips ?? []} volumeRanges={edl?.audio?.volume_ranges} look={edl?.look} gain={edl?.audio?.gain} />
        {edl && <BrollLayer broll={edl.broll} />}
        {edl?.progress_bar && <ProgressBar totalFrames={edl.total_frames} />}
        {edl && <Captions captions={edl.captions} style={edl.caption_style} options={edl.caption_options} />}
        {edl && <TextStickers overlays={edl.overlays} captions={edl.captions} captionStyle={edl.caption_style} captionOptions={edl.caption_options} />}
        {edl && <Grade look={edl.look} transitions={edl.transitions} />}
        {edl && <EndCard endCard={edl.end_card} />}
        <AudioMix audio={edl?.audio} />
      </AbsoluteFill>
    );
  }

  const schedule = edl?.react_schedule ?? [];
  const treatment = edl?.layout?.speaker_treatment === "pip_rounded_rect" ? "rect" : "circle";
  const position = edl?.layout?.pip_position ?? "bottom_left";

  const pipW = treatment === "circle" ? PIP_CIRCLE_DIA : PIP_RECT_W;
  const pipH = treatment === "circle" ? PIP_CIRCLE_DIA : PIP_RECT_H;
  const pipStyle: React.CSSProperties = {
    position: "absolute",
    width: pipW, height: pipH,
    bottom: PIP_MARGIN_BOTTOM,
    ...(position === "bottom_right" ? { right: PIP_MARGIN_X }
      : position === "bottom_center" ? { left: (LAYOUT.FRAME_W - pipW) / 2 }
      : { left: PIP_MARGIN_X }),
    borderRadius: treatment === "circle" ? "50%" : 24,
    overflow: "hidden",
    border: "5px solid rgba(255,255,255,0.85)",
    boxShadow: "0 16px 44px rgba(0,0,0,0.55)",
    background: "#000",
  };

  return (
    <AbsoluteFill style={{ background: "#000" }}>
      {/* SOURCE — full-frame, play/freeze per schedule (same grammar as DuetSplit's top panel) */}
      <AbsoluteFill>
        {react.kind === "image"
          ? <SourceImage url={react.resolved_url} durationInFrames={edl?.total_frames ?? 300} />
          : <SourceVideo url={react.resolved_url} schedule={schedule} />}
      </AbsoluteFill>
      {react.credit_label ? (
        <div style={{
          position: "absolute", top: LAYOUT.CREDIT_CHIP_TOP_PX, left: 32,
          padding: "8px 14px", borderRadius: 999, background: "rgba(0,0,0,0.55)",
          color: "#fff", fontFamily: "Inter, sans-serif", fontSize: 26, fontWeight: 600,
        }}>{react.credit_label}</div>
      ) : null}

      {/* SPEAKER PIP — protected layer: no punch zoom, text never covers it (captions sit
          in the mid band; stickers are clamped by their own safe box). */}
      <div style={pipStyle}>
        <div style={{
          position: "absolute",
          // Fill the PIP with the speaker cut: oversize the 9:16 video so the FACE region
          // (upper-center of a selfie framing) lands inside the small window.
          width: pipW * 2.2, height: (pipW * 2.2) * (16 / 9),
          left: -(pipW * 0.6), top: -(pipH * 0.25),
        }}>
          <CutVideo sourceUrl={sourceUrl} clips={edl?.clips ?? []} volumeRanges={edl?.audio?.volume_ranges} look={edl?.look} gain={edl?.audio?.gain} />
        </div>
      </div>

      {edl?.progress_bar && <ProgressBar totalFrames={edl.total_frames} />}
      {edl && <Captions captions={edl.captions} style={edl.caption_style} options={edl.caption_options} />}
      {edl && <TextStickers overlays={edl.overlays} captions={edl.captions} captionStyle={edl.caption_style} captionOptions={edl.caption_options} />}
      {edl && <Grade look={edl.look} transitions={edl.transitions} />}
      {edl && <EndCard endCard={edl.end_card} />}
      <AudioMix audio={edl?.audio} />
    </AbsoluteFill>
  );
};

// Ken-Burns still (screenshot/tweet as the source).
const SourceImage: React.FC<{ url: string; durationInFrames: number }> = ({ url, durationInFrames }) => {
  const frame = useCurrentFrame();
  const scale = interpolate(frame, [0, durationInFrames], [1.0, 1.08], { extrapolateRight: "clamp" });
  return <Img src={url} style={{ width: "100%", height: "100%", objectFit: "cover", transform: `scale(${scale})` }} />;
};

// Play/freeze schedule (output coords) — identical grammar to DuetSplit's ReactVideo:
// freeze windows hold the exact frame under discussion; play windows carry the source's
// own (ducked) audio per Part 5's one-primary-source rule.
const SourceVideo: React.FC<{ url: string; schedule: ReactWindow[] }> = ({ url, schedule }) => {
  if (schedule.length === 0) {
    return <OffthreadVideo src={url} volume={0.12}
      style={{ width: "100%", height: "100%", objectFit: "cover" }} />;
  }
  return (
    <>
      {schedule.map((w, i) => (
        <Sequence key={i} from={w.frame_in} durationInFrames={Math.max(1, w.frame_out - w.frame_in)} layout="none">
          {w.state === "freeze" ? (
            <Freeze frame={w.clip_from}>
              <OffthreadVideo src={url} muted
                style={{ position: "absolute", inset: 0, width: "100%", height: "100%", objectFit: "cover" }} />
            </Freeze>
          ) : (
            <OffthreadVideo src={url} trimBefore={w.clip_from}
              volume={Math.max(0, Math.min(1, w.audio_gain))}
              style={{ position: "absolute", inset: 0, width: "100%", height: "100%", objectFit: "cover" }} />
          )}
        </Sequence>
      ))}
    </>
  );
};
