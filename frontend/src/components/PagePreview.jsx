import { useMemo } from 'react';
import { PAGE_SIZES, PAGE_MARGIN_PX } from '@/lib/pageSizes';
import { sanitizeHtml } from '@/lib/sanitize';

// Perceived luminance — same rule used by the Editor so cover thumbnails
// auto-flip page-number color to match the canvas.
export function isDarkHex(hex) {
  if (!hex || typeof hex !== 'string') return false;
  const h = hex.replace('#', '');
  const v = h.length === 3 ? h.split('').map((c) => c + c).join('') : h;
  if (v.length !== 6) return false;
  const r = parseInt(v.slice(0, 2), 16);
  const g = parseInt(v.slice(2, 4), 16);
  const b = parseInt(v.slice(4, 6), 16);
  const L = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255;
  return L < 0.55;
}

// Renders a single book page composition (image + text blocks) scaled into
// a fixed-width box. Used by both the editor's page sidebar and the
// dashboard library cards so what you see in the library matches the
// designed cover exactly — title, author and artwork all rendered together.
export default function PagePreview({
  page,
  pageSizeKey = 'a4',
  width,
  showPageNumber = false,
  pageNumberValue = null,
  assetCacheBuster = 0,
  className = '',
  style = {},
}) {
  const pageSize = PAGE_SIZES[pageSizeKey] || PAGE_SIZES.a4;
  const scale = width / pageSize.width;
  const height = pageSize.height * scale;
  const backendBase = process.env.REACT_APP_BACKEND_URL || '';

  const blocks = useMemo(() => page?.blocks || [], [page]);
  const bg = page?.background_color || '#F9F6F0';
  const fullBleed = !!page?.full_bleed;

  return (
    <div
      className={className}
      style={{
        width,
        height,
        position: 'relative',
        background: '#FFFFFF',
        overflow: 'hidden',
        ...style,
      }}
      data-testid="page-preview"
    >
      <div
        aria-hidden
        style={{
          position: 'absolute',
          top: fullBleed ? 0 : PAGE_MARGIN_PX * scale,
          left: fullBleed ? 0 : PAGE_MARGIN_PX * scale,
          width: (pageSize.width - (fullBleed ? 0 : PAGE_MARGIN_PX * 2)) * scale,
          height: (pageSize.height - (fullBleed ? 0 : PAGE_MARGIN_PX * 2)) * scale,
          background: bg,
        }}
      />
      <div
        style={{
          width: pageSize.width,
          height: pageSize.height,
          transform: `scale(${scale})`,
          transformOrigin: 'top left',
          position: 'absolute',
          top: 0,
          left: 0,
          pointerEvents: 'none',
        }}
      >
        {blocks.map((b) => {
          const isText = b.type === 'text';
          return (
            <div
              key={b.id}
              style={{
                position: 'absolute',
                left: b.x,
                top: b.y,
                width: b.width,
                ...(isText ? { minHeight: b.height } : { height: b.height }),
                overflow: isText ? 'visible' : 'hidden',
                zIndex: b.z_index || 0,
              }}
            >
              {isText ? (
                <div
                  style={{
                    fontFamily: b.font_family,
                    fontSize: b.font_size,
                    textAlign: b.text_align,
                    color: b.color,
                    lineHeight: 1.4,
                  }}
                  dangerouslySetInnerHTML={{ __html: sanitizeHtml(b.html || '') }}
                />
              ) : b.image_url ? (
                <img
                  alt=""
                  src={(() => {
                    const base = b.image_url.startsWith('http') ? b.image_url : `${backendBase}${b.image_url}`;
                    return assetCacheBuster
                      ? `${base}${base.includes('?') ? '&' : '?'}v=${assetCacheBuster}`
                      : base;
                  })()}
                  style={{ width: '100%', height: '100%', objectFit: 'contain' }}
                />
              ) : null}
            </div>
          );
        })}
        {showPageNumber && pageNumberValue != null && (
          <div
            className="absolute"
            style={{
              fontFamily: page.page_number_font || 'Cormorant Garamond',
              fontSize: page.page_number_size || 14,
              color: isDarkHex(bg) ? '#E8E2D4' : '#3A3833',
              bottom: (fullBleed ? 0 : PAGE_MARGIN_PX) + 16,
              left: (page.page_number_align || 'right') === 'left' ? (fullBleed ? 0 : PAGE_MARGIN_PX) + 16 : undefined,
              right: (page.page_number_align || 'right') === 'right' ? (fullBleed ? 0 : PAGE_MARGIN_PX) + 16 : undefined,
              ...((page.page_number_align || 'right') === 'center'
                ? { left: 0, right: 0, textAlign: 'center' }
                : {}),
            }}
          >
            {pageNumberValue}
          </div>
        )}
      </div>
    </div>
  );
}
