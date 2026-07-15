import React from "react";
import { AbsoluteFill, Sequence, OffthreadVideo, Img, Freeze, useCurrentFrame, interpolate } from "remotion";
import { CutVideo } from "../components/CutVideo";
import { usePunchScale } from "../components/PunchZoom";
import { AudioMix } from "../components/AudioMix";
import { TextStickers } from "../components/TextStickers";
import { BrollLayer } from "../components/BrollLayer";
import { Grade } from "../components/Grade";
import { Captions, FONTS } from "../components/Captions";
import { ProgressBar } from "../components/ProgressBar";
import { EndCard } from "../components/EndCard";
import { CompositionProps, ReactWindow } from "../types";
import { LAYOUT, cardFit } from "../layout";

// Stacked 9:16 react split. TOP panel = the reacted-to clip, driven by a play/freeze/duck
// schedule (it plays with audio during "play" windows, freezes on a still + ducks audio
// while the creator rebuts). BOTTOM panel = the creator's talking head (always-on voice).
// Pull-quote text_cards pin the claim being rebutted; a payoff punch-in intensifies the
// creator on the final beat. Falls back to a full-frame creator cut if no react source.
export const DuetSplit: React.FC<CompositionProps> = ({ sourceUrl, edl }) => {
  const frame = useCurrentFrame();
  const react = edl?.react_source;
  const topFrac = edl?.layout?.split_fraction ?? 0.58;

  if (!react?.resolved_url) {
    // No source clip provided — degrade to a plain talking-head cut so it still renders.
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
  const quoteCards = (edl?.overlays ?? []).filter((o) => o.type === "text_card");
  // Was a hand-duplicated copy of usePunchScale's ramp math that had drifted (missing
  // the narrow-window fix) — use the shared hook so this can't drift again.
  const bottomScale = usePunchScale(edl?.overlays);
  const activeCard = quoteCards.find((o) => frame >= o.frame_in && frame < o.frame_out);
  const frozenNow = schedule.some(
    (w) => w.state === "freeze" && frame >= w.frame_in && frame < w.frame_out
  );
  // Formatting fix #9: shrink-to-fit + a hard line-clamp stop so a long pull-quote
  // can't overflow the top panel.
  const quoteUsablePx = LAYOUT.FRAME_W - 64 - 44; // left/right 32px inset minus internal padding
  const quoteFit = activeCard
    ? cardFit(activeCard.text, 30, quoteUsablePx, LAYOUT.QUOTE_MAX_LINES, LAYOUT.QUOTE_MIN_FONT)
    : null;

  return (
    <AbsoluteFill style={{ background: "#000" }}>
      {/* TOP panel — the reacted-to clip */}
      <div style={{ position: "absolute", top: 0, left: 0, width: "100%",
        height: `${topFrac * 100}%`, overflow: "hidden", background: "#000" }}>
        {react.kind === "image"
          ? <ReactImage url={react.resolved_url} durationInFrames={edl?.total_frames ?? 300} />
          : <ReactVideo url={react.resolved_url} schedule={schedule} />}
        {/* frozen-state scrim so a paused source reads as intentional */}
        {frozenNow && <div style={{ position: "absolute", inset: 0,
          background: "rgba(10,12,20,0.32)" }} />}
        {/* source attribution chip — moved down from top:24 (formatting fix #5: that
            sat inside the platform's top status-bar/pill chrome on every host app) */}
        {react.credit_label ? (
          <div style={{ position: "absolute", top: LAYOUT.CREDIT_CHIP_TOP_PX, left: 24, padding: "6px 14px",
            background: "rgba(0,0,0,0.55)", color: "white", borderRadius: 999,
            fontFamily: FONTS.inter, fontSize: 26, fontWeight: 600 }}>{react.credit_label}</div>
        ) : null}
        {/* pull-quote of the exact claim being rebutted */}
        {activeCard && quoteFit ? (
          <div style={{
            position: "absolute", left: 32, right: 32, bottom: 28,
            background: "white", borderRadius: 16, padding: "16px 22px", color: "#111",
            fontFamily: FONTS.inter, fontSize: quoteFit.fontSize, fontWeight: 700, textAlign: "center",
            boxShadow: "0 8px 24px rgba(0,0,0,0.35)",
            display: "-webkit-box", WebkitLineClamp: LAYOUT.QUOTE_MAX_LINES,
            WebkitBoxOrient: "vertical", overflow: "hidden",
          }}>{activeCard.text}</div>
        ) : null}
      </div>

      {/* divider hairline */}
      <div style={{ position: "absolute", top: `${topFrac * 100}%`, left: 0, width: "100%",
        height: 3, background: "rgba(255,255,255,0.14)" }} />

      {/* BOTTOM panel — the creator */}
      <div style={{ position: "absolute", bottom: 0, left: 0, width: "100%",
        height: `${(1 - topFrac) * 100}%`, overflow: "hidden", background: "#000" }}>
        <div style={{ position: "absolute", inset: 0, transform: `scale(${bottomScale})` }}>
          <CutVideo sourceUrl={sourceUrl} clips={edl?.clips ?? []} volumeRanges={edl?.audio?.volume_ranges} look={edl?.look} gain={edl?.audio?.gain} />
        </div>
      </div>

      {edl && <BrollLayer broll={edl.broll} />}
      {edl?.progress_bar && <ProgressBar totalFrames={edl.total_frames} />}
      {edl && <Captions captions={edl.captions} style={edl.caption_style} options={edl.caption_options} />}
      {edl && <TextStickers overlays={edl.overlays} captions={edl.captions} captionStyle={edl.caption_style} captionOptions={edl.caption_options} />}
      {edl && <Grade look={edl.look} transitions={edl.transitions} />}
      {edl && <EndCard endCard={edl.end_card} />}
      <AudioMix audio={edl?.audio} />
    </AbsoluteFill>
  );
};

// A still image source (screenshot of a tweet/post) with a slow Ken-Burns push over the
// clip's actual length.
const ReactImage: React.FC<{ url: string; durationInFrames: number }> = ({ url, durationInFrames }) => {
  const frame = useCurrentFrame();
  const scale = interpolate(frame, [0, durationInFrames], [1.0, 1.08], { extrapolateRight: "clamp" });
  return <Img src={url} style={{ position: "absolute", inset: 0, width: "100%",
    height: "100%", objectFit: "cover", transform: `scale(${scale})` }} />;
};

// A video source gated by the play/freeze/duck schedule: each window is its own Sequence —
// "play" advances the clip with its audio at audio_gain; "freeze" pins it on a still frame,
// muted. Windows tile the whole top-panel timeline.
const ReactVideo: React.FC<{ url: string; schedule: ReactWindow[] }> = ({ url, schedule }) => {
  if (schedule.length === 0) {
    // No play/freeze schedule (e.g. every window was cut-desynced away): keep the
    // source visible but DUCKED — unmodified it played at full volume over the
    // creator's entire voiceover, which is two people talking at once for the
    // whole video. 0.12 matches the freeze-window duck level.
    return <OffthreadVideo src={url} volume={0.12} style={coverStyle} />;
  }
  return (
    <>
      {schedule.map((w, i) => {
        const dur = Math.max(1, w.frame_out - w.frame_in);
        return (
          <Sequence key={i} from={w.frame_in} durationInFrames={dur} layout="none">
            {w.state === "freeze" ? (
              <Freeze frame={w.clip_from}>
                <OffthreadVideo src={url} muted style={coverStyle} />
              </Freeze>
            ) : (
              <OffthreadVideo src={url} trimBefore={w.clip_from}
                volume={Math.max(0, Math.min(1, w.audio_gain))} style={coverStyle} />
            )}
          </Sequence>
        );
      })}
    </>
  );
};

const coverStyle: React.CSSProperties = {
  position: "absolute", inset: 0, width: "100%", height: "100%", objectFit: "cover",
};
