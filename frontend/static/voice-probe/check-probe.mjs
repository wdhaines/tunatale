/**
 * Headless end-to-end check of the wake-word probe (tunatale-mkyb).
 *
 *   cd frontend
 *   ./static/voice-probe/fetch-assets.sh                      # once
 *   node static/voice-probe/check-probe.mjs --make-stimuli    # once (needs uv + ffmpeg)
 *   node static/voice-probe/check-probe.mjs
 *
 * NOT a Playwright spec, and deliberately so: it needs ~19 MB of gitignored
 * assets, so as a committed spec it would be skipped in CI for ever — a test
 * that never runs is worse than a script someone runs on purpose.
 *
 * WHY IT EXISTS. The spotter's failure mode is silence: a wrong buffer stride
 * or a 48 kHz AudioContext produces plausible low scores for ever and nothing
 * throws. Discovering that in a parked car costs a trip. This drives the real
 * page with a fake microphone — ort load, getUserMedia, AudioWorklet reframing,
 * all three ONNX models, threshold, WAKE — so the arithmetic is confirmed
 * against the browser it will actually run in.
 *
 * ⚠️ It runs BOTH stimuli, and the negative one is the point. A spotter stuck
 * at 1.0 also fires on the wake word; only the pair separates "it works" from
 * "it is jammed on". Measured 2026-09-02 on this machine: 0.999 vs 0.001.
 *
 * What it does NOT measure: anything about the phone. CPU here is a Mac's, and
 * the pause/focus behaviour that this whole epic turns on is Android's alone.
 */
import { spawnSync } from "node:child_process";
import { createServer } from "node:http";
import { readFile, stat } from "node:fs/promises";
import { dirname, extname, join, normalize, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(HERE, "..");            // frontend/static
const STIM = join(HERE, "stimuli");
const SECONDS = Number(process.env.CHECK_SECONDS || 30);

// The negative stimulus is Norwegian on purpose: the qi5.1 spike showed the
// cloud recognizer hallucinating confident English from Norwegian TTS
// ("by the cabin", conf=0.797), so L2 speech is the adversary that matters.
const CASES = [
  { name: "positive", phrase: "Hey Jarvis", file: "wake.wav", expect: "wake" },
  { name: "negative", phrase: "Jeg vil gjerne ha en kopp kaffe takk", file: "nowake.wav", expect: "silence" },
];

if (process.argv.includes("--make-stimuli")) {
  const py = `
import asyncio, subprocess, sys
from pathlib import Path
out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
async def tts(text, dest):
    import edge_tts
    await edge_tts.Communicate(text, "en-US-AndrewNeural").save(str(dest))
for phrase, name in [(sys.argv[2], "wake"), (sys.argv[3], "nowake")]:
    mp3 = out / f"{name}.mp3"
    if not mp3.exists(): asyncio.run(tts(phrase, mp3))
    # 1.5 s of silence either side: Chrome LOOPS the fake-capture file, and the
    # spotter's receptive field is 1.96 s, so back-to-back utterances would never
    # present a clean window. 16-bit PCM mono 16 kHz — a format Chrome rejects
    # yields a SILENT stream rather than an error.
    subprocess.run(["ffmpeg","-y","-v","error","-i",str(mp3),
                    "-af","adelay=1500|1500,apad=pad_dur=1.5",
                    "-ac","1","-ar","16000","-c:a","pcm_s16le", str(out / f"{name}.wav")], check=True)
    print("wrote", out / f"{name}.wav")
`;
  const r = spawnSync("uv", ["run", "--with", "edge-tts", "python", "-c", py, STIM, CASES[0].phrase, CASES[1].phrase],
    { stdio: "inherit" });
  process.exit(r.status ?? 1);
}

const MIME = {
  ".html": "text/html", ".js": "text/javascript", ".mjs": "text/javascript",
  ".wasm": "application/wasm", ".onnx": "application/octet-stream",
};
const server = createServer(async (req, res) => {
  const path = normalize(decodeURIComponent(req.url.split("?")[0]));
  // The probe's durable sink is a backend route this server does not have. 404
  // is its documented "switched off" answer, so the page degrades to on-screen
  // logging exactly as it does when client_log_enabled is false.
  if (path.startsWith("/api/")) return void res.writeHead(404).end();
  try {
    const file = join(ROOT, path);
    if (!(await stat(file)).isFile()) throw new Error("dir");
    res.writeHead(200, { "content-type": MIME[extname(file)] || "application/octet-stream" });
    res.end(req.method === "HEAD" ? undefined : await readFile(file));
  } catch { res.writeHead(404).end(); }
});
await new Promise((r) => server.listen(0, "127.0.0.1", r));
const base = `http://127.0.0.1:${server.address().port}`;

async function run({ name, file, expect, phrase }) {
  const wav = join(STIM, file);
  try { await stat(wav); }
  catch { console.log(`\n${name}: MISSING ${wav} — run with --make-stimuli first`); return false; }

  const browser = await chromium.launch({
    args: [
      "--use-fake-ui-for-media-stream",
      "--use-fake-device-for-media-stream",
      `--use-file-for-fake-audio-capture=${wav}`,
      "--autoplay-policy=no-user-gesture-required",
    ],
  });
  const page = await browser.newPage({ permissions: ["microphone"] });
  const console_ = [];
  page.on("pageerror", (e) => console_.push(`PAGEERROR ${e.message}`));

  await page.goto(`${base}/voice-probe.html`);
  await page.waitForSelector("#wakearm");
  // BURST off: headless Chromium has no SpeechRecognition, and the question
  // here is the spotter, not the burst.
  await page.uncheck("#wakeburst");
  await page.selectOption("#wakeword", "hey_jarvis_v0.1");
  await page.click("#wakearm");

  const dump = async (why) => {
    const log = await page.evaluate(() => [...document.querySelectorAll("#log div")].map((d) => d.textContent).join("\n"));
    console.log(`\n${name}: ${why}\n${log}\n${console_.join("\n")}`);
  };
  try {
    await page.waitForFunction(() => document.querySelector("#wakearm").textContent === "DISARM SPOTTER",
      null, { timeout: 120_000 });
  } catch {
    await dump("ARM never completed");
    await browser.close();
    return false;
  }

  let peak = 0;
  for (const t0 = Date.now(); Date.now() - t0 < SECONDS * 1000; ) {
    peak = Math.max(peak, await page.evaluate(() => parseFloat(document.querySelector("#wakenum").textContent) || 0));
    await page.waitForTimeout(200);
  }
  await page.click("#wakearm");               // DISARM writes the summary line
  await page.waitForTimeout(500);

  const text = await page.evaluate(() => [...document.querySelectorAll("#log div")].map((d) => d.textContent).join("\n"));
  const wakes = (text.match(/WAKE while SILENT|WAKE \[MARKED\]/g) || []).length;
  const summary = (text.match(/WAKE DISARM.*/) || ["(no summary line)"])[0];
  const errs = text.split("\n").filter((l) => /inference failed|melspectrogram returned|not 16000|dropped chunk|failed to load/i.test(l));

  const pass = expect === "wake" ? wakes > 0 && peak > 0.5 : wakes === 0 && peak < 0.5;
  console.log(`\n── ${name} (${expect}): "${phrase}"`);
  console.log(`   peak=${peak.toFixed(3)}  wakes=${wakes} in ${SECONDS}s`);
  console.log(`   ${summary.replace(/^\[[\d.]+s\] /, "")}`);
  for (const e of errs.slice(0, 5)) console.log(`   ERR ${e}`);
  for (const e of console_.slice(0, 3)) console.log(`   ${e}`);
  console.log(`   ${pass ? "PASS" : "*** FAIL ***"}`);

  await browser.close();
  return pass;
}

let ok = true;
for (const c of CASES) ok = (await run(c)) && ok;
server.close();
console.log(ok ? "\nAll checks passed." : "\nFAILED — see above.");
process.exit(ok ? 0 : 1);
