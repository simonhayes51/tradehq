import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import Workspace from "./Workspace";

describe("V4 workspace", () => {
 it("exposes primary navigation and its content", () => {
  render(<MemoryRouter initialEntries={["/"]}><Workspace><h1>Trading home</h1></Workspace></MemoryRouter>);
  expect(screen.getByRole("heading",{name:"Trading home"})).toBeTruthy();
  expect(screen.getByRole("navigation",{name:"Desktop navigation"})).toBeTruthy();
  expect(screen.getByRole("navigation",{name:"Mobile primary navigation"})).toBeTruthy();
  expect(screen.getAllByRole("link",{name:/trades/i}).length).toBeGreaterThan(0);
 });
});
