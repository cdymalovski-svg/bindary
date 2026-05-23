// Page size definitions in pixels at 96 DPI.
// width x height (portrait).
export const PAGE_SIZES = {
  a4: { label: 'A4', width: 794, height: 1123 },
  letter: { label: 'US Letter', width: 816, height: 1056 },
  square: { label: 'Square', width: 800, height: 800 },
  book6x9: { label: '6" × 9"', width: 576, height: 864 },
};

export const getPageSize = (key) => PAGE_SIZES[key] || PAGE_SIZES.a4;
