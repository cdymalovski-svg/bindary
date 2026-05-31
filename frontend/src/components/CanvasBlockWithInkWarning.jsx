import { useInkWarning } from '@/hooks/useInkWarning';
import CanvasBlock from './CanvasBlock';

/**
 * Tiny wrapper that resolves the per-block ink warning and forwards it
 * into `CanvasBlock`. Kept separate so the hook (which can fire image-
 * sampling work) is mounted/unmounted with each block, and so the
 * computation runs only for the blocks currently on screen.
 */
export default function CanvasBlockWithInkWarning(props) {
  const { block, pageBg, imageBase, inkWarningsEnabled } = props;
  const { overdrawn } = useInkWarning({
    block,
    pageBg,
    imageBase,
    enabled: !!inkWarningsEnabled,
  });
  // Strip the extra props before forwarding so CanvasBlock's signature
  // stays the same.
  const { pageBg: _b, imageBase: _i, inkWarningsEnabled: _e, ...rest } = props;
  return <CanvasBlock {...rest} inkOverdrawn={overdrawn} />;
}
