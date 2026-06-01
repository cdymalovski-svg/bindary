// Page size definitions in pixels at 96 DPI.
// width x height (portrait).
export const PAGE_SIZES = {
  a4: { label: 'A4', width: 794, height: 1123 },
  letter: { label: 'US Letter', width: 816, height: 1056 },
  square: { label: 'Square', width: 800, height: 800 },
  book6x9: { label: '6" × 9"', width: 576, height: 864 },
};

export const getPageSize = (key) => PAGE_SIZES[key] || PAGE_SIZES.a4;

// 1cm at 96 DPI = 37.795275591 px. We use 37.8 for clean rendering.
export const CM_PX = 37.8;
// 0.5" inner safety margin. 0.5" × 96 DPI = 48 px exactly. This is the
// blank "trim safety" gutter around the coloured area — content placed
// inside this margin will print but risks getting too close to the page
// edge for commercial printing. Designers should keep important content
// inside the visible dashed safe-zone in the editor.
export const PAGE_MARGIN_PX = 48;
export const PAGE_MARGIN_IN = 0.5;
