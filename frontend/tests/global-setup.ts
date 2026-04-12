// Global setup runs AFTER webServer starts in Playwright's task order.
// DB cleanup is handled in the webServer command itself (rm -f tunatale-test.db).
// This file is kept as a hook point for any future pre-test setup that doesn't
// depend on the webServer being up.
export default function globalSetup() {
	// intentionally empty
}
