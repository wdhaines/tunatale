export type VoiceCommand =
  | "TOGGLE_PLAY"
  | "SEEK_BACK_10"
  | "SEEK_FORWARD_10"
  | "NEXT_CUE"
  | "PREV_CUE"
  | "REPEAT_CUE"
  | "RESTART_SECTION"
  | "ENUN_SLOWER"
  | "ENUN_FASTER"
  | "TOGGLE_ENGLISH"
  | "PHASE_KEY_PHRASES"
  | "PHASE_DIALOGUE";

const PHRASE_TABLE: Record<string, VoiceCommand> = {
  play: "TOGGLE_PLAY",
  pause: "TOGGLE_PLAY",
  "back ten": "SEEK_BACK_10",
  "back 10": "SEEK_BACK_10",
  "forward ten": "SEEK_FORWARD_10",
  "forward 10": "SEEK_FORWARD_10",
  "next sentence": "NEXT_CUE",
  "last sentence": "PREV_CUE",
  again: "REPEAT_CUE",
  repeat: "REPEAT_CUE",
  "start over": "RESTART_SECTION",
  slower: "ENUN_SLOWER",
  faster: "ENUN_FASTER",
  english: "TOGGLE_ENGLISH",
  "key phrases": "PHASE_KEY_PHRASES",
  dialogue: "PHASE_DIALOGUE",
};

function normalize(utterance: string): string {
  const trimmed = utterance
    .trim()
    .replace(/\s+/g, " ")
    .replace(/^[.,!?]+|[.,!?]+$/g, "");
  return trimmed.toLowerCase();
}

export function resolveCommand(utterance: string): VoiceCommand | null {
  const key = normalize(utterance);
  // Object.hasOwn, not a bare index: a plain object literal inherits from
  // Object.prototype, so `PHRASE_TABLE["constructor"]` is the Object
  // constructor and `PHRASE_TABLE["__proto__"]` is Object.prototype. Neither is
  // nullish, so `?? null` passes them straight through and the function returns
  // something that is not a VoiceCommand — while the declared return type says
  // it cannot. Only the already-lowercase members leak, because normalize()
  // lowercases first: `toString` becomes `tostring` and misses.
  // "constructor" is an ordinary English word a recognizer can return.
  return Object.hasOwn(PHRASE_TABLE, key) ? PHRASE_TABLE[key] : null;
}
