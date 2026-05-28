import { useLayoutEffect, useRef, useState } from 'react';

/**
 * Renders text that scales its font-size up or down so the FULL string fits
 * the available width on a single line. No ellipsis — never truncates.
 *
 * Algorithm: binary-search font-size between `min` and `max` px and pick the
 * largest one whose rendered width is <= the container width. Re-runs when
 * the text, the container width, or any of the props change.
 *
 * Why a custom component instead of pure CSS: native `clamp()` doesn't know
 * how wide the rendered text will be, so it can't fit-to-content. The only
 * reliable way is to measure.
 */
export default function FitText({
  children,
  min = 8,
  max = 24,
  className = '',
  style,
  as: Tag = 'p',
  'data-testid': testId,
}) {
  const containerRef = useRef(null);
  const measureRef = useRef(null);
  const [fontSize, setFontSize] = useState(max);

  useLayoutEffect(() => {
    const container = containerRef.current;
    const measure = measureRef.current;
    if (!container || !measure) return undefined;

    const fit = () => {
      const available = container.clientWidth;
      if (available <= 0) return;
      // Binary search 0.5 px precision — fast and visually smooth.
      let lo = min;
      let hi = max;
      while (hi - lo > 0.5) {
        const mid = (lo + hi) / 2;
        measure.style.fontSize = `${mid}px`;
        if (measure.scrollWidth <= available) lo = mid;
        else hi = mid;
      }
      setFontSize(lo);
    };

    fit();
    const ro = new ResizeObserver(fit);
    ro.observe(container);
    return () => ro.disconnect();
  }, [children, min, max]);

  return (
    <Tag
      ref={containerRef}
      className={className}
      style={{ ...style, fontSize, lineHeight: 1.1, whiteSpace: 'nowrap', overflow: 'hidden' }}
      data-testid={testId}
    >
      {/*
        Hidden mirror used for measurement. Positioned absolutely so it
        doesn't affect layout. Inherits font-family from parent so the
        measurement matches the visible glyph metrics exactly.
      */}
      <span
        ref={measureRef}
        aria-hidden
        style={{
          position: 'absolute',
          visibility: 'hidden',
          whiteSpace: 'nowrap',
          pointerEvents: 'none',
        }}
      >
        {children}
      </span>
      {children}
    </Tag>
  );
}
