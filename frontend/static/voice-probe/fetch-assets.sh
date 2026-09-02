#!/bin/bash
# Fetch the wake-word spike's third-party assets (tunatale-mkyb).
#
# These are NOT committed: ~3.7 MB of ONNX plus the onnxruntime-web WASM runtime
# is real repo weight for a probe, and every byte of it is a pinned, immutable
# release artifact that re-downloads in seconds. The probe's preflight reports
# whether they are present and prints this command when they are not, so a
# missing asset is a labelled row rather than a silent failure.
#
# Everything lands under frontend/static/voice-probe/, which src/service-worker.ts
# explicitly excludes from the PWA app-shell precache — see swPrecacheAssets().
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# openWakeWord v0.5.1 — the shared front end (melspectrogram + Google speech
# embedding) plus one head per wake word. Pinned to a tag, not a branch.
OWW_BASE="https://github.com/dscripka/openWakeWord/releases/download/v0.5.1"
OWW_MODELS=(melspectrogram embedding_model hey_jarvis_v0.1 alexa_v0.1 hey_mycroft_v0.1)

# onnxruntime-web. The probe loads ort.wasm.min.js and the runtime then fetches
# its own .wasm/.mjs siblings from the same directory (ort.env.wasm.wasmPaths).
#
# ⚠️ The WASM-ONLY build, deliberately. The default `ort.min.js` pulls
# `ort-wasm-simd-threaded.jsep.wasm` — **27.8 MB**, because it carries the
# WebGPU/WebNN execution providers. This probe runs three tiny models on a
# phone's CPU and will never dispatch to the GPU, so that is 14 MB of download
# over Tailscale buying nothing. The CPU build is 14.0 MB and the loader 50 KB.
ORT_VERSION="1.29.0"
ORT_BASE="https://cdn.jsdelivr.net/npm/onnxruntime-web@${ORT_VERSION}/dist"
ORT_FILES=(
  ort.wasm.min.js
  ort-wasm-simd-threaded.mjs
  ort-wasm-simd-threaded.wasm
)

get () {  # get <url> <dest>
  if [ -s "$2" ]; then
    printf '  ok   %-42s (cached)\n' "$(basename "$2")"
    return
  fi
  curl -fsSL --retry 3 -o "$2.part" "$1"
  mv "$2.part" "$2"
  printf '  got  %-42s %s bytes\n' "$(basename "$2")" "$(wc -c < "$2" | tr -d ' ')"
}

mkdir -p "$HERE/models" "$HERE/ort"

echo "openWakeWord v0.5.1 models -> voice-probe/models/"
for m in "${OWW_MODELS[@]}"; do
  get "$OWW_BASE/$m.onnx" "$HERE/models/$m.onnx"
done

echo "onnxruntime-web $ORT_VERSION -> voice-probe/ort/"
for f in "${ORT_FILES[@]}"; do
  get "$ORT_BASE/$f" "$HERE/ort/$f"
done

echo
echo "Done. Open https://<host>:5173/voice-probe.html and check the preflight."
