import React from 'react';
import {Composition, type CalculateMetadataFunction} from 'remotion';
import {defaultOverlayProps} from './defaultProps';
import {Overlay} from './Overlay';
import {overlayPropsSchema, type OverlayProps} from './schema';

/** fps, duration and size always come from the props (the Python timeline is the source of truth). */
const calculateMetadata: CalculateMetadataFunction<OverlayProps> = ({props}) => ({
  durationInFrames: props.durationInFrames,
  fps: props.fps,
  width: props.width,
  height: props.height,
  props,
});

export const RemotionRoot: React.FC = () => (
  <Composition
    id="Overlay"
    component={Overlay}
    schema={overlayPropsSchema}
    defaultProps={defaultOverlayProps}
    calculateMetadata={calculateMetadata}
    durationInFrames={defaultOverlayProps.durationInFrames}
    fps={defaultOverlayProps.fps}
    width={defaultOverlayProps.width}
    height={defaultOverlayProps.height}
  />
);
