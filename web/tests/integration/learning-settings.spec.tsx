import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import LearningSettingsSection from "@/features/settings/sections/LearningSettingsSection";

const settings = vi.hoisted(() => ({
  enabled: true,
  update: vi.fn<(next: boolean) => Promise<void>>(),
}));

vi.mock("@/features/settings/store", () => ({
  useUiSettings: () => ({
    learningCoordinatorEnabled: settings.enabled,
    updateLearningCoordinatorEnabled: settings.update,
  }),
}));

describe("LearningSettingsSection", () => {
  beforeEach(() => {
    settings.enabled = true;
    settings.update.mockReset();
    settings.update.mockResolvedValue();
  });

  it("reflects and updates the personal Learning Coordinator setting", async () => {
    // Break caught: the switch ignores the saved opt-in or writes anywhere except the UI-settings action.
    const user = userEvent.setup();
    render(<LearningSettingsSection />);

    const toggle = screen.getByRole("switch", {
      name: "Use the Learning Coordinator",
    });
    expect(toggle).toHaveAttribute("aria-checked", "true");

    await user.click(toggle);
    expect(settings.update).toHaveBeenCalledWith(false);
  });

  it("announces a translated persistence failure without an unhandled rejection", async () => {
    // Production break caught: the toggle discards the rejecting promise, so
    // rollback is silent and the browser reports an unhandled rejection.
    settings.update.mockRejectedValue(new Error("private transport detail"));
    const user = userEvent.setup();
    render(<LearningSettingsSection />);

    await user.click(
      screen.getByRole("switch", { name: "Use the Learning Coordinator" }),
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Learning setting could not be saved. Please try again.",
    );
    expect(screen.queryByText(/private transport/)).not.toBeInTheDocument();
  });
});
