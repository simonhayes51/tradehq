import "@testing-library/jest-dom";

// recharts' <ResponsiveContainer> observes its parent's size, and jsdom
// has no ResizeObserver. Without this any test that renders a chart dies
// with "ResizeObserver is not defined" before it can assert anything.
// A no-op is enough: the container falls back to its declared height and
// the SVG still renders into the DOM.
if (typeof globalThis.ResizeObserver === "undefined") {
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
}
