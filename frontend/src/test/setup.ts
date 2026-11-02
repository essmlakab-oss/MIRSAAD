/**
 * jsdom polyfills for the component specs.
 *
 * Recharts' ResponsiveContainer needs ResizeObserver; the strip runs in a
 * scrollable container jsdom renders but does not scroll-simulate.
 */

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

if (typeof globalThis.ResizeObserver === "undefined") {
  globalThis.ResizeObserver = ResizeObserverStub as unknown as typeof ResizeObserver;
}

// jsdom throws on scrollTo without arguments and has no layout; the strip
// falls back to scrollLeft assignment.
Object.defineProperty(HTMLElement.prototype, "scrollTo", {
  value(bounds: ScrollToOptions = {}) {
    if (typeof bounds.left === "number") this.scrollLeft = bounds.left;
    if (typeof bounds.top === "number") this.scrollTop = bounds.top;
  },
  configurable: true,
  writable: true,
});
