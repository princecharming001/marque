#!/usr/bin/env node
// Thin CLI bridge so the Python backend can trigger/poll Remotion Lambda renders.
// Remotion's render API (renderMediaOnLambda / getRenderProgress) is Node-only — there
// is no documented cross-language wire contract for invoking a deployed Lambda function
// directly, so this script is the integration point: the Python backend shells out to
// the compiled JS here instead of calling a REST endpoint.
//
// Usage:
//   node dist/lambda-render.js submit <compositionId> <inputPropsJson> [preview]
//     -> prints {"renderId": "...", "bucketName": "..."} to stdout
//     -> preview="1": G9 cheap low-res proof render (half scale, higher CRF) —
//        for the manual editor's "show me before I commit" HD-preview button;
//        NOT the final render, and the backend never writes this to render_url.
//   node dist/lambda-render.js poll <renderId> <bucketName>
//     -> prints {"done": bool, "overallProgress": 0-1, "outputFile": "..."|null, ...}
//
// Required env: REMOTION_AWS_ACCESS_KEY_ID, REMOTION_AWS_SECRET_ACCESS_KEY,
// REMOTION_FUNCTION_NAME (from `npx remotion lambda functions deploy`),
// REMOTION_SERVE_URL (from `npx remotion lambda sites create`), REMOTION_AWS_REGION
// (defaults to us-east-1).

import { renderMediaOnLambda, getRenderProgress } from "@remotion/lambda/client";
import type { AwsRegion } from "@remotion/lambda/client";

const REGION = (process.env.REMOTION_AWS_REGION || "us-east-1") as AwsRegion;
const FUNCTION_NAME = process.env.REMOTION_FUNCTION_NAME || "";
const SERVE_URL = process.env.REMOTION_SERVE_URL || "";
// P0.2: final-render encode quality. crf 17 + jpegQuality 95 kill the caption halos the
// q80 intermediate JPEGs caused, at ~10% Lambda cost. Env-tunable so a cost/quality dial
// stays backend-only. (Preview renders keep their cheap scale:0.5 / crf:30 path.)
const JPEG_QUALITY = Number(process.env.REMOTION_JPEG_QUALITY || "95");
const IMAGE_FORMAT = (process.env.REMOTION_IMAGE_FORMAT || "jpeg") as "jpeg" | "png";

async function submit(compositionId: string, inputPropsJson: string, preview?: string) {
  if (!FUNCTION_NAME) throw new Error("REMOTION_FUNCTION_NAME is not set");
  if (!SERVE_URL) throw new Error("REMOTION_SERVE_URL is not set");
  const inputProps = JSON.parse(inputPropsJson);
  const isPreview = preview === "1";
  const { renderId, bucketName } = await renderMediaOnLambda({
    region: REGION,
    functionName: FUNCTION_NAME,
    serveUrl: SERVE_URL,
    composition: compositionId,
    inputProps,
    codec: "h264",
    // G9: half resolution + higher compression — same composition/timing logic,
    // just cheaper/faster pixels. Never touches width/height/fps/duration, so
    // total_frames-driven durationInFrames (calculateMetadata) stays identical.
    // P0.2: final renders get crf 17 + high-quality intermediate JPEGs (no halos).
    ...(isPreview
      ? { scale: 0.5, crf: 30, outName: "preview.mp4" }
      : { crf: 17, imageFormat: IMAGE_FORMAT, jpegQuality: JPEG_QUALITY }),
  });
  process.stdout.write(JSON.stringify({ renderId, bucketName }));
}

async function poll(renderId: string, bucketName: string) {
  if (!FUNCTION_NAME) throw new Error("REMOTION_FUNCTION_NAME is not set");
  const progress = await getRenderProgress({
    renderId,
    bucketName,
    functionName: FUNCTION_NAME,
    region: REGION,
  });
  process.stdout.write(JSON.stringify({
    done: progress.done,
    overallProgress: progress.overallProgress,
    fatalErrorEncountered: progress.fatalErrorEncountered,
    errors: progress.errors?.map((e) => e.message) ?? [],
    outputFile: progress.outputFile ?? null,
  }));
}

const [, , cmd, a, b, c] = process.argv;

(async () => {
  try {
    if (cmd === "submit") await submit(a, b, c);
    else if (cmd === "poll") await poll(a, b);
    else throw new Error(`Unknown command "${cmd}" — use "submit <compositionId> <inputPropsJson> [preview]" or "poll <renderId> <bucketName>"`);
  } catch (err) {
    process.stderr.write(JSON.stringify({ error: err instanceof Error ? err.message : String(err) }));
    process.exitCode = 1;
  }
})();
