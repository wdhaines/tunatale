import { describe, expect, it } from "vitest";

import { isPrecachableAsset, precacheAssets } from "./precache";

describe("isPrecachableAsset", () => {
  it("keeps the built app shell", () => {
    expect(isPrecachableAsset("/_app/immutable/entry/start.abc123.js")).toBe(true);
  });

  it("keeps ordinary static files", () => {
    expect(isPrecachableAsset("/manifest.webmanifest")).toBe(true);
    expect(isPrecachableAsset("/icon-512.png")).toBe(true);
    expect(isPrecachableAsset("/robots.txt")).toBe(true);
  });

  it("drops the debug probe page", () => {
    expect(isPrecachableAsset("/voice-probe.html")).toBe(false);
  });

  it("drops everything under the probe's asset directory", () => {
    // The wake-word spike carries ~19 MB of ONNX models and the
    // onnxruntime-web WASM runtime. Precaching those would push them into
    // every user's app-shell cache on install.
    expect(isPrecachableAsset("/voice-probe/models/hey_jarvis_v0.1.onnx")).toBe(false);
    expect(isPrecachableAsset("/voice-probe/ort/ort-wasm-simd-threaded.wasm")).toBe(false);
    expect(isPrecachableAsset("/voice-probe/fetch-assets.sh")).toBe(false);
  });

  it("does not drop an unrelated file whose name merely starts the same way", () => {
    // Guards the difference between a prefix test and a path-segment test:
    // `/voice-probes-report.html` is not inside `/voice-probe/`.
    expect(isPrecachableAsset("/voice-probes-report.html")).toBe(true);
  });
});

describe("precacheAssets", () => {
  it("concatenates build and files, filtered", () => {
    expect(
      precacheAssets(["/_app/immutable/x.js"], ["/icon-512.png", "/voice-probe.html"]),
    ).toEqual(["/_app/immutable/x.js", "/icon-512.png"]);
  });
});
