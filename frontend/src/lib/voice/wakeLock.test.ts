import { describe, it, expect, vi } from "vitest";

import { createWakeLock } from "./wakeLock";

function fakeSentinel(release: () => Promise<void> = () => Promise.resolve()) {
  return { release };
}

function fakeNavigator(request: () => Promise<{ release: () => Promise<void> }>) {
  return { wakeLock: { request } };
}

describe("createWakeLock", () => {
  it("acquires a screen wake lock when enabled", async () => {
    const request = vi.fn(() => Promise.resolve(fakeSentinel()));
    const wakeLock = createWakeLock(() => fakeNavigator(request));
    await wakeLock.sync(true);
    expect(request).toHaveBeenCalledTimes(1);
    expect(request).toHaveBeenCalledWith("screen");
  });

  it("does not acquire twice while already holding a sentinel", async () => {
    const request = vi.fn(() => Promise.resolve(fakeSentinel()));
    const wakeLock = createWakeLock(() => fakeNavigator(request));
    await wakeLock.sync(true);
    await wakeLock.sync(true);
    expect(request).toHaveBeenCalledTimes(1);
  });

  it("releases when disabled, and re-enabling acquires again", async () => {
    const release = vi.fn(() => Promise.resolve());
    const request = vi.fn(() => Promise.resolve(fakeSentinel(release)));
    const wakeLock = createWakeLock(() => fakeNavigator(request));
    await wakeLock.sync(true);
    await wakeLock.sync(false);
    expect(release).toHaveBeenCalledTimes(1);
    await wakeLock.sync(true);
    expect(request).toHaveBeenCalledTimes(2);
  });

  it("release() releases whatever a destroy path left held", async () => {
    const release = vi.fn(() => Promise.resolve());
    const request = vi.fn(() => Promise.resolve(fakeSentinel(release)));
    const wakeLock = createWakeLock(() => fakeNavigator(request));
    await wakeLock.sync(true);
    await wakeLock.release();
    expect(release).toHaveBeenCalledTimes(1);
  });

  it("release() with nothing held is a silent no-op", async () => {
    const wakeLock = createWakeLock(() => ({}));
    await expect(wakeLock.release()).resolves.toBeUndefined();
    await expect(wakeLock.sync(false)).resolves.toBeUndefined();
  });

  it("absent navigator.wakeLock is a silent no-op", async () => {
    const wakeLock = createWakeLock(() => ({}));
    await expect(wakeLock.sync(true)).resolves.toBeUndefined();
  });

  it("swallows a rejected request (document not visible) without holding anything", async () => {
    const request = vi.fn(() => Promise.reject(new Error("not visible")));
    const wakeLock = createWakeLock(() => fakeNavigator(request));
    await expect(wakeLock.sync(true)).resolves.toBeUndefined();
    await expect(wakeLock.sync(false)).resolves.toBeUndefined();
    expect(request).toHaveBeenCalledTimes(1);
  });

  it("swallows a rejected release so playback is never broken", async () => {
    const release = vi.fn(() => Promise.reject(new Error("stale sentinel")));
    const request = vi.fn(() => Promise.resolve(fakeSentinel(release)));
    const wakeLock = createWakeLock(() => fakeNavigator(request));
    await wakeLock.sync(true);
    await expect(wakeLock.sync(false)).resolves.toBeUndefined();
  });
});
