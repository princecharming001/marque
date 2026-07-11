import { Config } from "@remotion/cli/config";
// P0.2: env-tunable intermediate-frame quality (mirrors the Lambda path in
// lambda-render.ts). Default jpegQuality 95 kills caption halos on CLI renders too.
Config.setVideoImageFormat((process.env.REMOTION_IMAGE_FORMAT as "jpeg" | "png") || "jpeg");
Config.setJpegQuality(Number(process.env.REMOTION_JPEG_QUALITY || "95"));
Config.setPixelFormat("yuv420p");
Config.setCodec("h264");
Config.setOverwriteOutput(true);
