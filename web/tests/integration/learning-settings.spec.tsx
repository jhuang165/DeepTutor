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
});
