import type { MutableRefObject } from 'react';

export interface WorldControls {
  pointer: { x: number; y: number };
  drag: number;
  ripple: number;
  focused: boolean;
  /** Cinematic authentication progress, animated from 0 to 1 by GSAP. */
  progress: number;
  reducedMotion: boolean;
}

export interface WorldProps {
  controls: MutableRefObject<WorldControls>;
  mobile: boolean;
}
