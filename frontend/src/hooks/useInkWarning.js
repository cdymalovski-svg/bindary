import { useEffect, useState } from 'react';
import {
  INK_LIMIT_PERCENT,
  estimateImageInkTotal,
  estimateTextBlockInkTotal,
} from '@/lib/inkCoverage';

/**
 * Returns `{ overdrawn, totalInk }` for a block when the global ink-coverage
 * warning is enabled. Text blocks resolve synchronously; image blocks load
 * the asset into an offscreen canvas to compute the average CMYK total.
 *
 * Designed to be a no-op when `enabled === false` so we don't pay the
 * canvas / fetch cost in the default editor state.
 */
export function useInkWarning({ block, pageBg, imageBase, enabled }) {
  const [imageInk, setImageInk] = useState(null);

  useEffect(() => {
    if (!enabled || block?.type !== 'image' || !block?.image_url) {
      setImageInk(null);
      return;
    }
    let cancelled = false;
    const src = block.image_url.startsWith('http')
      ? block.image_url
      : `${imageBase || ''}${block.image_url}`;
    estimateImageInkTotal(src).then((avg) => {
      if (!cancelled) setImageInk(avg);
    });
    return () => { cancelled = true; };
  }, [enabled, block?.type, block?.image_url, imageBase]);

  if (!enabled) return { overdrawn: false, totalInk: null };

  if (block?.type === 'text') {
    const total = estimateTextBlockInkTotal(block, pageBg);
    return { overdrawn: total > INK_LIMIT_PERCENT, totalInk: total };
  }
  if (block?.type === 'image') {
    if (imageInk == null) return { overdrawn: false, totalInk: null };
    return { overdrawn: imageInk > INK_LIMIT_PERCENT, totalInk: imageInk };
  }
  return { overdrawn: false, totalInk: null };
}
