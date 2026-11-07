import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import V4Link from "./V4Link";

describe("V4 preview links",()=>{
 it("keeps rebuilt routes inside the isolated preview",()=>{
  render(<MemoryRouter initialEntries={["/preview-v4/portfolio"]}><V4Link to="/players/123">Player</V4Link><V4Link to="/portfolio">Portfolio</V4Link></MemoryRouter>);
  expect(screen.getByRole("link",{name:"Player"}).getAttribute("href")).toBe("/preview-v4/players/123");
  expect(screen.getByRole("link",{name:"Portfolio"}).getAttribute("href")).toBe("/preview-v4/portfolio");
 });
 it("leaves unfinished routes on the existing app",()=>{
  render(<MemoryRouter initialEntries={["/preview-v4"]}><V4Link to="/billing">Billing</V4Link></MemoryRouter>);
  expect(screen.getByRole("link",{name:"Billing"}).getAttribute("href")).toBe("/billing");
 });
});
