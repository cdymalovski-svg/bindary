// Curated font palette grouped by category.
// All Google Fonts (loaded via index.css @import) + a few common system fonts.

export const FONT_GROUPS = [
  {
    label: 'Serif',
    fonts: [
      'Cormorant Garamond',
      'EB Garamond',
      'Playfair Display',
      'Lora',
      'Merriweather',
      'Libre Baskerville',
      'Crimson Text',
      'Bitter',
      'Georgia',
      'Times New Roman',
    ],
  },
  {
    label: 'Sans-serif',
    fonts: [
      'Outfit',
      'Montserrat',
      'Poppins',
      'Raleway',
      'Work Sans',
      'DM Sans',
      'Nunito',
      'Helvetica',
    ],
  },
  {
    label: 'Display',
    fonts: [
      'Abril Fatface',
      'Bebas Neue',
      'Lobster',
      'Pacifico',
    ],
  },
  {
    label: 'Handwritten',
    fonts: [
      'Caveat',
      'Dancing Script',
      'Sacramento',
      'Indie Flower',
      'Kalam',
    ],
  },
  {
    label: 'Monospace',
    fonts: [
      'JetBrains Mono',
      'Fira Code',
      'Inconsolata',
      'Courier New',
    ],
  },
];

// Flat list (convenient for quick lookups).
export const ALL_FONTS = FONT_GROUPS.flatMap((g) => g.fonts);
