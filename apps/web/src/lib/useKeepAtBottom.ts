import { useCallback, useEffect, useRef, type RefObject } from "react";

/** Closer than this to the end counts as "at the end": following resumes. */
const AT_END_PX = 80;

export interface KeepAtBottom {
  /** Whether the container is held at its end. */
  following: RefObject<boolean>;
  setFollowing: (following: boolean) => void;
  /** For the container's scroll events; answers whether it is still following. */
  readScroll: () => boolean;
}

/**
 * Keeps a scroll container at its end while the reader is there, as its content keeps growing
 * after the jump: rendered markdown, code blocks and virtualised rows measured after an estimated
 * height all change the height afterwards.
 *
 * Only the reader moving up leaves the end. A scroll event is dispatched a frame after the scroll,
 * so one caused by holding the end may already measure the next growth as distance from it.
 * Browser scroll anchoring is off while following: it shifts the position when rows above the
 * viewport are measured, and is wanted back once the reader has scrolled up (older messages are
 * prepended above them).
 */
export function useKeepAtBottom(containerRef: RefObject<HTMLElement | null>, mounted: boolean): KeepAtBottom {
  const following = useRef(true);
  const lastTop = useRef(0);

  const setFollowing = useCallback(
    (value: boolean) => {
      following.current = value;
      const container = containerRef.current;
      if (container) container.style.overflowAnchor = value ? "none" : "";
    },
    [containerRef]
  );

  const readScroll = useCallback(() => {
    const container = containerRef.current;
    if (!container) return following.current;
    const top = container.scrollTop;
    if (container.scrollHeight - top - container.clientHeight <= AT_END_PX) setFollowing(true);
    else if (top < lastTop.current) setFollowing(false);
    lastTop.current = top;
    return following.current;
  }, [containerRef, setFollowing]);

  useEffect(() => {
    const container = containerRef.current;
    if (!mounted || !container) return;
    setFollowing(following.current);
    const pin = () => {
      if (!following.current) return;
      container.scrollTop = container.scrollHeight;
      lastTop.current = container.scrollTop;
    };
    // The container's own box does not grow with its content; its children's boxes do.
    const resizes = new ResizeObserver(pin);
    const observeChildren = () => {
      resizes.disconnect();
      for (const child of Array.from(container.children)) resizes.observe(child);
    };
    const additions = new MutationObserver(() => {
      observeChildren();
      pin();
    });
    observeChildren();
    additions.observe(container, { childList: true });
    pin();
    return () => {
      resizes.disconnect();
      additions.disconnect();
    };
  }, [containerRef, mounted, setFollowing]);

  return { following, setFollowing, readScroll };
}
