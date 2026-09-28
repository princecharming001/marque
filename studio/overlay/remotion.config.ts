/**
 * Defaults for `npx remotion render` / `npx remotion studio` run from this folder. The Python renderer
 * (studio/compile/overlays.py) passes every flag explicitly anyway, so these only matter for manual use.
 * Transparent ProRes 4444: PNG frames (JPEG has no alpha) -> yuva444p10le, BT.709 matrix/tags (Remotion's
 * v4 default colour space is bt601, which would shift every caption colour).
 */
import {Config} from '@remotion/cli/config';

Config.setVideoImageFormat('png');
Config.setPixelFormat('yuva444p10le');
Config.setCodec('prores');
Config.setProResProfile('4444');
Config.setColorSpace('bt709');
Config.setMuted(true);
Config.setOverwriteOutput(true);
Config.setEntryPoint('src/index.ts');
