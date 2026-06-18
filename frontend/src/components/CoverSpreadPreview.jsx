import { useEffect, useRef, useState } from 'react';

/**
 * Read-only cover-spread preview.
 *
 * Mirrors the geometry of `_build_cover_spread_html` (backend):
 *   ┌──────────────────────────────────────────────┐
 *   │      OUTER ALLOWANCE  (bleed or wrap)        │
 *   │   ┌─────────┬──────┬──────────┐              │
 *   │   │  BACK   │SPINE │  FRONT   │              │
 *   │   └─────────┴──────┴──────────┘              │
 *   │      OUTER ALLOWANCE                         │
 *   └──────────────────────────────────────────────┘
 *
 * Plus overlay markers a designer wants to see BEFORE exporting:
 *   • Trim line   — where the cutter cuts (solid terracotta)
 *   • Safe margin — 0.5" inset from trim (dashed grey-green)
 *   • Spine folds — vertical dotted lines flanking the spine
 *   • Casebound:
 *       – Wrap fold (= trim) — solid line where the cover folds inside
 *       – Board outline — inset from trim by 0.083" (board edge sits
 *         visually slightly inside the wrap so the eye sees the board
 *         beneath the printed paper).
 *
 * Reads `binding` and explicit `spineWidthIn` from props (kept in sync
 * with ExportPopover via the parent that owns localStorage). Pure
 * presentational — never mutates the book.
 */

// Geometry constants — must mirror backend pdf_builder.py.
const PX_PER_INCH = 96;
const COVER_BLEED_IN = 0.125;
const CASEBOUND_WRAP_IN = 0.625;
const CASEBOUND_SPINE_ALLOWANCE_IN = 0.125;
const DEFAULT_PAPER_CALIPER_IN = 0.002252;
const CASEBOUND_BOARD_THICKNESS_IN = 0.083; // visual marker only
const SAFE_MARGIN_IN = 0.5;

function computeGeometry({ pageWidth, pageHeight, totalPages, binding, spineWidthIn }) {
  const isCase = binding === 'casebound';
  const outerIn = isCase ? CASEBOUND_WRAP_IN : COVER_BLEED_IN;
  const outerPx = Math.round(outerIn * PX_PER_INCH);

  // Spine — explicit override OR auto-compute from interior page count.
  let spineIn = Number.isFinite(spineWidthIn) && spineWidthIn > 0 ? spineWidthIn : null;
  if (spineIn === null) {
    const interior = Math.max(0, totalPages - 2);
    spineIn = interior * DEFAULT_PAPER_CALIPER_IN;
    if (isCase) spineIn += CASEBOUND_SPINE_ALLOWANCE_IN;
  }
  const spinePx = Math.max(4, Math.round(spineIn * PX_PER_INCH));

  const trimWidth = pageWidth * 2 + spinePx;
  const trimHeight = pageHeight;
  const totalWidth = trimWidth + outerPx * 2;
  const totalHeight = trimHeight + outerPx * 2;

  return {
    isCase,
    outerIn,
    outerPx,
    spineIn,
    spinePx,
    trimWidth,
    trimHeight,
    totalWidth,
    totalHeight,
    safePx: Math.round(SAFE_MARGIN_IN * PX_PER_INCH),
    boardInsetPx: Math.round(CASEBOUND_BOARD_THICKNESS_IN * PX_PER_INCH),
  };
}

/** Single static page rendered to scale — no react-rnd, no editing. */
function StaticCoverPage({ page, pageSize, totalPages, label }) {
  const bg = page.background_color || '#FFF8DC';
  return (
    <div
      className="relative overflow-hidden"
      style={{
        width: `${pageSize.width}px`,
        height: `${pageSize.height}px`,
        background: bg,
      }}
      data-testid={`cover-spread-${label}`}
    >
      {(page.blocks || [])
        .slice()
        .sort((a, b) => (a.z_index || 0) - (b.z_index || 0))
        .map((block) => (
          <div
            key={block.id}
            style={{
              position: 'absolute',
              left: `${block.x}px`,
              top: `${block.y}px`,
              width: `${block.width}px`,
              height: `${block.height}px`,
              zIndex: block.z_index || 1,
              overflow: 'hidden',
            }}
          >
            {block.type === 'text' ? (
              <div
                style={{
                  width: '100%',
                  height: '100%',
                  padding: '4px 8px',
                  boxSizing: 'border-box',
                  fontFamily: `${block.font_family || 'Cormorant Garamond'}, serif`,
                  fontSize: `${block.font_size || 18}px`,
                  textAlign: block.text_align || 'left',
                  color: block.color || '#000000',
                  lineHeight: 1.45,
                  overflow: 'hidden',
                  wordWrap: 'break-word',
                  overflowWrap: 'break-word',
                }}
                /* Stored HTML is DOMPurify-sanitised on save (see Editor). */
                // eslint-disable-next-line react/no-danger
                dangerouslySetInnerHTML={{ __html: block.html || '' }}
              />
            ) : block.type === 'image' && (block.image_url || block.image_path) ? (
              <img
                src={
                  block.image_url ||
                  `${process.env.REACT_APP_BACKEND_URL || ''}/api/files/${block.image_path}`
                }
                alt=""
                draggable={false}
                style={{
                  width: '100%',
                  height: '100%',
                  objectFit: 'contain',
                  display: 'block',
                  background: block.background_color || 'transparent',
                }}
              />
            ) : block.type === 'image' && block.background_color ? (
              <div
                style={{ width: '100%', height: '100%', background: block.background_color }}
              />
            ) : null}
          </div>
        ))}
      {/* Subtle corner badge so the designer can tell which side they're
          looking at without the back/front banner above (helpful on tiny
          viewports where the banner gets clipped). */}
      <span
        className="absolute top-1 right-1 text-[9px] uppercase tracking-widest text-ink-mute bg-paper/80 px-1 py-0.5 rounded-sm pointer-events-none"
      >
        {label}
      </span>
      {/* Suppress unused-var warning */}
      {totalPages ? null : null}
    </div>
  );
}

export default function CoverSpreadPreview({
  book,
  pageSize,
  binding = 'perfect',
  spineWidthIn = null,
  onBindingChange = null,
  onSpineWidthChange = null,
  showSafeMargin = true,
  showTrim = true,
  showBoardOutline = true,
}) {
  const pages = book?.pages || [];
  const total = pages.length;
  const front = pages[0];
  const back = pages[total - 1];

  const geom = computeGeometry({
    pageWidth: pageSize.width,
    pageHeight: pageSize.height,
    totalPages: total,
    binding,
    spineWidthIn,
  });

  // Fit-to-viewport scale (same idea as PageCanvas). Recomputes on resize.
  const containerRef = useRef(null);
  const [fitScale, setFitScale] = useState(0.4);
  useEffect(() => {
    const compute = () => {
      const padding = 96;
      const sidebars = 224 + 288;
      const availW = window.innerWidth - sidebars - padding;
      const availH = window.innerHeight - 56 - padding;
      const s = Math.min(
        1,
        availW / geom.totalWidth,
        availH / geom.totalHeight,
      );
      setFitScale(Math.max(0.18, s));
    };
    compute();
    window.addEventListener('resize', compute);
    return () => window.removeEventListener('resize', compute);
  }, [geom.totalWidth, geom.totalHeight]);

  if (!front || !back) {
    return (
      <div className="p-12 text-ink-mute text-sm italic" data-testid="cover-spread-preview-empty">
        Add at least two pages to see the cover spread.
      </div>
    );
  }

  // Spine background harmonises with the front cover (same rule the
  // backend uses for the PDF spread).
  const spineBg = front.background_color || '#1C1B19';

  const interactive = onBindingChange || onSpineWidthChange;

  return (
    <div className="flex flex-col items-center gap-4">
      {/* Optional in-preview toolbar — visible when the parent passes
          change handlers. Lets the designer flip binding / set spine
          width without leaving the canvas. */}
      {interactive && (
        <div
          className="flex items-center gap-3 bg-paper border border-rule rounded-sm px-3 py-2 text-xs"
          data-testid="cover-spread-toolbar"
        >
          <div className="flex items-center gap-1 bg-rule/30 rounded-sm p-0.5" role="radiogroup" aria-label="Binding">
            <button
              type="button"
              onClick={() => onBindingChange && onBindingChange('perfect')}
              aria-pressed={binding === 'perfect'}
              data-testid="cover-preview-binding-perfect"
              className={`h-6 px-2 rounded-sm text-[11px] font-medium transition-colors ${
                binding === 'perfect' ? 'bg-ink text-paper' : 'text-ink-soft hover:bg-white'
              }`}
            >
              Perfect-bound
            </button>
            <button
              type="button"
              onClick={() => onBindingChange && onBindingChange('casebound')}
              aria-pressed={binding === 'casebound'}
              data-testid="cover-preview-binding-casebound"
              className={`h-6 px-2 rounded-sm text-[11px] font-medium transition-colors ${
                binding === 'casebound' ? 'bg-ink text-paper' : 'text-ink-soft hover:bg-white'
              }`}
            >
              Casebound
            </button>
          </div>
          {onSpineWidthChange && (
            <label className="flex items-center gap-2 text-ink-mute">
              <span className="uppercase tracking-widest text-[10px]">Spine</span>
              <input
                type="number"
                step="0.001"
                min={0}
                placeholder="auto"
                value={spineWidthIn != null ? spineWidthIn : ''}
                onChange={(e) => {
                  const v = parseFloat(e.target.value);
                  onSpineWidthChange(Number.isFinite(v) && v > 0 ? v : null);
                }}
                className="h-6 w-20 bg-white border border-rule rounded-sm px-1.5 text-xs"
                data-testid="cover-preview-spine-width"
              />
              <span className="text-[10px]">in</span>
            </label>
          )}
        </div>
      )}

      <div
        ref={containerRef}
        className="relative"
        style={{
          width: `${geom.totalWidth * fitScale}px`,
          height: `${geom.totalHeight * fitScale}px`,
        }}
        data-testid="cover-spread-preview"
      >
      {/* Specification chip — always visible so the designer sees the
          live geometry at a glance. */}
      <div
        className="absolute -top-9 left-0 right-0 flex items-center justify-between text-[10px] text-ink-mute"
        data-testid="cover-spread-chip"
      >
        <span className="uppercase tracking-widest">
          {binding === 'casebound' ? 'Casebound hardcover spread' : 'Perfect-bound cover spread'}
        </span>
        <span>
          Trim {(geom.trimWidth / PX_PER_INCH).toFixed(2)}″ × {(geom.trimHeight / PX_PER_INCH).toFixed(2)}″
          {' · '}Spine {geom.spineIn.toFixed(3)}″
          {' · '}{binding === 'casebound' ? `Wrap ${CASEBOUND_WRAP_IN}″` : `Bleed ${COVER_BLEED_IN}″`}
        </span>
      </div>

      {/* Inner full-size canvas, scaled down to fit. We pin the
          transform-origin to top-left so the chip + canvas top edges
          stay aligned with the container. */}
      <div
        style={{
          width: `${geom.totalWidth}px`,
          height: `${geom.totalHeight}px`,
          transform: `scale(${fitScale})`,
          transformOrigin: 'top left',
        }}
      >
        {/* Layer 0 — outer allowance background (spine-colour band so the
            wrapped/bled edges visually continue the spine). */}
        <div
          className="absolute inset-0 shadow-lg"
          style={{ background: spineBg }}
          data-testid="cover-spread-outer-band"
        />

        {/* Layer 1 — back cover, spine fill, front cover (sit ON TOP
            of the outer band, offset by `outerPx`). */}
        <div
          className="absolute flex"
          style={{
            top: `${geom.outerPx}px`,
            left: `${geom.outerPx}px`,
            width: `${geom.trimWidth}px`,
            height: `${geom.trimHeight}px`,
          }}
        >
          <StaticCoverPage page={back} pageSize={pageSize} totalPages={total} label="Back" />
          <div
            style={{
              width: `${geom.spinePx}px`,
              height: '100%',
              background: spineBg,
              position: 'relative',
            }}
            data-testid="cover-spread-spine"
          >
            <span
              className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 text-[9px] uppercase tracking-widest"
              style={{
                color: spineBg && parseInt(spineBg.replace('#', '').slice(0, 2), 16) < 128 ? '#E8E2D4' : '#3A3833',
                writingMode: 'vertical-rl',
                whiteSpace: 'nowrap',
              }}
            >
              Spine · {geom.spineIn.toFixed(3)}″
            </span>
          </div>
          <StaticCoverPage page={front} pageSize={pageSize} totalPages={total} label="Front" />
        </div>

        {/* Layer 2 — overlay markers. Pointer-events disabled so the
            user can still click through if we ever make this clickable. */}
        <svg
          className="absolute inset-0 pointer-events-none"
          width={geom.totalWidth}
          height={geom.totalHeight}
          viewBox={`0 0 ${geom.totalWidth} ${geom.totalHeight}`}
          data-testid="cover-spread-overlay"
        >
          {/* Trim rectangle — the cutter cuts here. Solid terracotta.
              For casebound this is ALSO the wrap-fold line — print
              outside this rectangle folds inside the boards. */}
          {showTrim && (
            <rect
              x={geom.outerPx}
              y={geom.outerPx}
              width={geom.trimWidth}
              height={geom.trimHeight}
              fill="none"
              stroke="#9E4532"
              strokeWidth={2}
              data-testid="cover-spread-trim"
            />
          )}
          {/* Spine fold lines — dotted ink lines flanking the spine, so
              the designer knows precisely where the printed paper folds
              over the spine board. */}
          <line
            x1={geom.outerPx + pageSize.width}
            x2={geom.outerPx + pageSize.width}
            y1={geom.outerPx}
            y2={geom.outerPx + geom.trimHeight}
            stroke="#1C1B19"
            strokeWidth={1}
            strokeDasharray="2 3"
          />
          <line
            x1={geom.outerPx + pageSize.width + geom.spinePx}
            x2={geom.outerPx + pageSize.width + geom.spinePx}
            y1={geom.outerPx}
            y2={geom.outerPx + geom.trimHeight}
            stroke="#1C1B19"
            strokeWidth={1}
            strokeDasharray="2 3"
          />
          {/* Safe-margin rectangle — 0.5" inset from trim. Anything
              outside this dashed line risks being cropped/trimmed off
              or sitting too close to the spine. */}
          {showSafeMargin && (
            <rect
              x={geom.outerPx + geom.safePx}
              y={geom.outerPx + geom.safePx}
              width={geom.trimWidth - geom.safePx * 2}
              height={geom.trimHeight - geom.safePx * 2}
              fill="none"
              stroke="#3A6E48"
              strokeWidth={1}
              strokeDasharray="4 4"
              data-testid="cover-spread-safe"
              opacity={0.7}
            />
          )}
          {/* Casebound only: board outline. Boards sit inset from the
              wrapped edge by ~0.083" — drawing the outline helps spot
              text/images that will be hidden by the case turn-in. */}
          {geom.isCase && showBoardOutline && (
            <rect
              x={geom.outerPx + geom.boardInsetPx}
              y={geom.outerPx + geom.boardInsetPx}
              width={geom.trimWidth - geom.boardInsetPx * 2}
              height={geom.trimHeight - geom.boardInsetPx * 2}
              fill="none"
              stroke="#9E4532"
              strokeWidth={1}
              strokeDasharray="6 3"
              opacity={0.45}
              data-testid="cover-spread-board"
            />
          )}
        </svg>
      </div>

      {/* Legend — pinned to the bottom of the container. Outside the
          scaled canvas so it stays readable at every zoom level. */}
      <div
        className="absolute -bottom-7 left-0 right-0 flex flex-wrap items-center gap-x-4 gap-y-1 text-[10px] text-ink-mute"
        data-testid="cover-spread-legend"
      >
        <span className="flex items-center gap-1">
          <span className="inline-block w-3 h-px bg-terracotta" /> Trim {geom.isCase && '/ wrap fold'}
        </span>
        <span className="flex items-center gap-1">
          <span
            className="inline-block w-3 h-px"
            style={{ borderTop: '1px dashed #3A6E48' }}
          />
          Safe margin (0.5″)
        </span>
        <span className="flex items-center gap-1">
          <span className="inline-block w-3 h-px" style={{ borderTop: '1px dotted #1C1B19' }} />
          Spine folds
        </span>
        {geom.isCase && (
          <span className="flex items-center gap-1">
            <span className="inline-block w-3 h-px" style={{ borderTop: '1px dashed #9E4532' }} />
            Board edge
          </span>
        )}
      </div>
    </div>
    </div>
  );
}
