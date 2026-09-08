import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { FormattedMessage } from "./Chat";

describe("chat message formatting", () => {
  it("renders bold-led inline summaries as readable bullets without exposing markdown syntax", () => {
    const { container } = render(
      <FormattedMessage content="Over the last 90 days: - **332 wins out of 363 recommendations** - **91.5% win rate** - **30.63% median ROI**" />,
    );

    expect(screen.getByText("332 wins out of 363 recommendations").tagName).toBe("STRONG");
    expect(screen.getByText("91.5% win rate").tagName).toBe("STRONG");
    expect(screen.getByText("30.63% median ROI").tagName).toBe("STRONG");
    expect(container.textContent).not.toContain("**");
    expect(container.querySelectorAll(".v2-chat-message-bullet")).toHaveLength(3);
  });
});
