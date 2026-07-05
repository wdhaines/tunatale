/**
 * jsdom doesn't implement media playback: the real HTMLMediaElement
 * play()/pause()/load() just print "Not implemented: HTMLMediaElement's
 * pause() method" to stderr whenever a player test reaches them (e.g.
 * LessonPlayer's destroy() pausing on unmount). Stub them so suite output
 * stays clean — behavioral assertions never rely on jsdom's media engine:
 * playbackController tests inject a fake audio element, and component tests
 * spy on these prototype methods (vi.spyOn shadows the stub the same way it
 * shadowed jsdom's original).
 */
import { vi } from "vitest";

Object.defineProperty(HTMLMediaElement.prototype, "play", {
  configurable: true,
  writable: true,
  value: vi.fn().mockResolvedValue(undefined),
});
Object.defineProperty(HTMLMediaElement.prototype, "pause", {
  configurable: true,
  writable: true,
  value: vi.fn(),
});
Object.defineProperty(HTMLMediaElement.prototype, "load", {
  configurable: true,
  writable: true,
  value: vi.fn(),
});
