// src/v2/components/Nav.jsx
//
// Thin wrapper around Sidebar, which handles both the persistent left
// rail (desktop) and the collapsed bottom tab bar (mobile, via CSS at
// the 800px breakpoint in tokens.css) as one component - there is no
// separate mobile nav component.
import Sidebar from "./Sidebar";

export default function Nav() {
  return <Sidebar />;
}
