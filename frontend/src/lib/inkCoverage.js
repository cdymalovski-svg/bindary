// Ink-coverage estimation for the "exceeds 240% total CMYK" print warning.
//
// Why this matters: most commercial printers (IngramSpark, KDP, Lulu) reject
// art whose total ink coverage exceeds 240%. Beyond that ink doesn't dry on
// paper — pages stick to each other and the printer rejects the file.
//
// Our conversion uses the standard "naive" RGB → CMYK formula (no ICC
// profile). It's a generous approximation: the actual Ghostscript pass at
// export time uses a real CMYK profile and may produce slightly lower
// numbers. We err on the side of warning the designer.

const HEX = /^#?([0-9a-f]{3}|[0-9a-f]{6})$/i;

export function hexToRgb(hex) {
  if (!hex || typeof hex !== 'string') return null;
  const m = HEX.exec(hex.trim());
  if (!m) return null;
  let v = m[1];
  if (v.length === 3) v = v.split('').map((c) => c + c).join('');
  return [
    parseInt(v.slice(0, 2), 16),
    parseInt(v.slice(2, 4), 16),
    parseInt(v.slice(4, 6), 16),
  ];
}

// Returns total CMYK ink (0..400 %). Pure-white = 0, pure-black = 100,
// "registration black" (all four inks 100%) = 400.
export function rgbToCmykTotal(r, g, b) {
  const R = r / 255, G = g / 255, B = b / 255;
  const K = 1 - Math.max(R, G, B);
  if (K >= 0.999) return 100; // straight black
  const C = (1 - R - K) / (1 - K);
  const M = (1 - G - K) / (1 - K);
  const Y = (1 - B - K) / (1 - K);
  return (C + M + Y + K) * 100;
}

export function hexInkTotal(hex) {
  const rgb = hexToRgb(hex);
  if (!rgb) return null;
  return rgbToCmykTotal(rgb[0], rgb[1], rgb[2]);
}

// Process-wide cache so we only sample each unique image URL once.
const _imageInkCache = new Map();

// Computes the AVERAGE total ink for an image by drawing it into a small
// canvas and sampling pixels on a grid. Returns 0..400. Caches by URL.
// Resolves to `null` if the image can't be loaded (CORS, network error).
export function estimateImageInkTotal(src) {
  if (!src) return Promise.resolve(null);
  const cached = _imageInkCache.get(src);
  if (cached !== undefined) return Promise.resolve(cached);

  return new Promise((resolve) => {
    const img = new Image();
    img.crossOrigin = 'anonymous';
    img.onload = () => {
      try {
        // Downsample to a small canvas — we only need an average, not
        // pixel-perfect accuracy. 64×64 = 4096 samples, plenty for a stable
        // mean, and ~instant to compute.
        const SIZE = 64;
        const canvas = document.createElement('canvas');
        canvas.width = SIZE;
        canvas.height = SIZE;
        const ctx = canvas.getContext('2d');
        ctx.drawImage(img, 0, 0, SIZE, SIZE);
        const { data } = ctx.getImageData(0, 0, SIZE, SIZE);
        let sum = 0;
        let count = 0;
        for (let i = 0; i < data.length; i += 4) {
          // Skip fully transparent pixels — they print as paper white.
          if (data[i + 3] < 8) continue;
          sum += rgbToCmykTotal(data[i], data[i + 1], data[i + 2]);
          count++;
        }
        const avg = count ? sum / count : 0;
        _imageInkCache.set(src, avg);
        resolve(avg);
      } catch {
        // CORS-tainted canvas or other failure — bail gracefully so the
        // editor doesn't show a false negative on every reload.
        _imageInkCache.set(src, null);
        resolve(null);
      }
    };
    img.onerror = () => {
      _imageInkCache.set(src, null);
      resolve(null);
    };
    img.src = src;
  });
}

// Heuristic for text blocks. We use the COLOR field (typically the most
// dominant ink); per-character span colors inside the html would require
// parsing every block on every change which isn't worth the budget for
// a developer-facing warning. Background color of the PAGE is added on
// top so a dark-on-dark block (e.g. dark text on a dark cover) is also
// flagged accurately.
export function estimateTextBlockInkTotal(block, pageBg) {
  // The most ink-dense element wins — either the foreground text or the
  // page background that bleeds behind it.
  const colors = [block?.color, pageBg].filter(Boolean);
  let max = 0;
  for (const c of colors) {
    const total = hexInkTotal(c);
    if (total != null && total > max) max = total;
  }
  return max;
}

export const INK_LIMIT_PERCENT = 240;
