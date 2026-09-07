import React, { useEffect, useRef } from 'react';

export function LightboxIcon({ type }) {
  if (type === 'close') {
    return (
      <svg className="image-lightbox-control-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
        <path d="M6 6 18 18M18 6 6 18" />
      </svg>
    );
  }

  const points = type === 'prev' ? '15 5 8 12 15 19' : '9 5 16 12 9 19';
  return (
    <svg className="image-lightbox-control-icon image-lightbox-arrow-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <polyline points={points} />
    </svg>
  );
}

function selectorLabel(item) {
  if (item.selectorLabel) return item.selectorLabel;
  if (item.id === 'original' || item.id === 'nft') return 'NFT';
  if (item.id === 'body') return 'Body';
  if (item.id === 'face') return 'Face';
  return item.label || item.id;
}

export default function ImageLightbox({ items, index, onClose, onChange, label, showSelector = false }) {
  const dialogRef = useRef(null);
  const closeButtonRef = useRef(null);
  const indexRef = useRef(index);
  const onCloseRef = useRef(onClose);
  const onChangeRef = useRef(onChange);
  indexRef.current = index;
  onCloseRef.current = onClose;
  onChangeRef.current = onChange;

  useEffect(() => {
    const previousOverflow = document.body.style.overflow;
    const previousPaddingRight = document.body.style.paddingRight;
    const previouslyFocused = document.activeElement;
    const scrollbarWidth = Math.max(0, window.innerWidth - document.documentElement.clientWidth);

    document.body.style.overflow = 'hidden';
    if (scrollbarWidth > 0) document.body.style.paddingRight = `${scrollbarWidth}px`;
    window.requestAnimationFrame(() => closeButtonRef.current?.focus());

    const onKeyDown = (event) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        onCloseRef.current();
        return;
      }
      if (event.key === 'ArrowLeft') {
        event.preventDefault();
        onChangeRef.current((indexRef.current - 1 + items.length) % items.length);
        return;
      }
      if (event.key === 'ArrowRight') {
        event.preventDefault();
        onChangeRef.current((indexRef.current + 1) % items.length);
        return;
      }
      if (event.key !== 'Tab') return;

      const focusable = Array.from(dialogRef.current?.querySelectorAll('button:not([disabled])') || []);
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };

    window.addEventListener('keydown', onKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      document.body.style.paddingRight = previousPaddingRight;
      window.removeEventListener('keydown', onKeyDown);
      previouslyFocused?.focus?.();
    };
  }, [items.length]);

  const item = items[index];
  const image = (
    <div className="image-lightbox-image-wrap">
      <img
        src={item.src}
        alt={item.alt}
        loading="eager"
        decoding="async"
        fetchPriority={item.id === 'original' || item.id === 'nft' ? 'high' : 'auto'}
      />
    </div>
  );

  return (
    <div
      ref={dialogRef}
      className={`image-lightbox${showSelector ? ' has-hero-selector' : ''}`}
      role="dialog"
      aria-modal="true"
      aria-label={label}
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <button ref={closeButtonRef} type="button" className="image-lightbox-close" aria-label="Close image viewer" onClick={onClose}><LightboxIcon type="close" /></button>
      {items.length > 1 && (
        <button
          type="button"
          className="image-lightbox-arrow image-lightbox-prev"
          aria-label="Previous image"
          onClick={() => onChange((index - 1 + items.length) % items.length)}
        ><LightboxIcon type="prev" /></button>
      )}

      {showSelector ? (
        <div className="image-lightbox-hero-shell">
          {image}
          <div className="image-lightbox-tabs" role="tablist" aria-label="Hero image type">
            {items.map((tabItem, tabIndex) => (
              <button
                key={tabItem.id || tabIndex}
                type="button"
                role="tab"
                aria-selected={tabIndex === index}
                className={tabIndex === index ? 'is-active' : ''}
                onClick={() => onChange(tabIndex)}
              >
                {selectorLabel(tabItem)}
              </button>
            ))}
          </div>
        </div>
      ) : image}

      {items.length > 1 && (
        <button
          type="button"
          className="image-lightbox-arrow image-lightbox-next"
          aria-label="Next image"
          onClick={() => onChange((index + 1) % items.length)}
        ><LightboxIcon type="next" /></button>
      )}
    </div>
  );
}
