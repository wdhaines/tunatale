**Slovene Authenticity Rules — Read Carefully**

You are generating **Slovene** (slovenščina), a South Slavic language with ~2 million speakers.
It is DISTINCT from Croatian, Serbian, and Bosnian. LLMs commonly confuse these — guard against
cross-contamination aggressively.

**Critical: Words that DO NOT EXIST in Slovene — NEVER use them:**
- NEVER "izvinite" — ALWAYS "oprostite"  (izvinite is Serbian/Croatian/Russian)
- NEVER "što" — ALWAYS "kaj"  (što is Croatian; Slovene uses kaj for "what/which/that")
- NEVER "nemoj" — ALWAYS "ne" or "ne delaj"  (nemoj is Serbo-Croatian)
- NEVER "hvala vam puno" — ALWAYS "hvala lepa" or "najlepša hvala"
- NEVER "doviđenja" — ALWAYS "nasvidenje" or "adijo"

**Orthography — Slovene has ONLY these special characters: č, š, ž**
- NEVER write "ć" or "đ" — those letters DO NOT EXIST in Slovene
- NEVER use Spanish/accented characters: á, é, í, ó, ú
- Only valid Slovene special letters: č (ch), š (sh), ž (zh)

**Grammar — Critical patterns:**
- "prosim" governs accusative case:
  CORRECT: "kavo, prosim" — WRONG: "kavno, prosim" (kavno is adjectival/neuter, not accusative)
  CORRECT: "pivo, prosim" — WRONG: "pivno, prosim"
- Dual number for exactly two people (Slovene is one of few languages with grammatical dual):
  CORRECT: "midva greva" (we two go) — WRONG: "mi gremo" (for just two people)
  CORRECT: "onadva gresta" (they two go) — WRONG: "ona grejo" (for just two)
  Common dual forms: midva/medve, vidva/vedve, onadva/onidve; gresta, sta, imata, sva

**Register — T-V distinction (tikanje / vikanje):**
- Strangers, service staff, cafés, shops, public settings: use vikanje (vi/vas/vam)
  Example: "Kaj boste vi?" "Prosim vas..." "Hvala vam."
- Friends, family, close peers: use tikanje (ti/te/ti)
  Example: "Kaj boš ti?" "Te prosim..." "Hvala ti."
- Default to vikanje for ALL service scenarios at A2 level
- Never mix tikanje and vikanje for the same character in the same scene

**Natural Slovene discourse markers (use these where natural):**
- "saj" — "you know / after all / because" (e.g., "Saj vem, da je drago.")
- "pa" — versatile connector/contrast: "and / but / well" (e.g., "Kaj pa vi?")
- "ja" — "yes" as casual filler (e.g., "Ja, seveda.", "Ja, prosim.")
- "no" — casual tag/affirmation (e.g., "Gremo, no?", "No, potem.")
- "evo" — "here you go / there it is" (e.g., "Evo, vaša kava.")
- "mhm" — agreement/acknowledgement

**Quality benchmarks:**
CORRECT: "Oprostite, bi lahko dobil kavo, prosim?" (vikanje service register, correct case)
CORRECT: "Kaj pa vi? Bi kaj še?" (natural "kaj" + discourse marker "pa")
CORRECT: "Ja, seveda — gremo skupaj, no?" (natural fillers)
CORRECT: "Evo, vaša kava. Prosim." (natural service handoff)
CORRECT: "Koliko stane, prosim?" (standard payment phrase)
WRONG: "Izvinite, prosim." (Serbian word — use "Oprostite")
WRONG: "Kavno brez mleka." (adjectival form — use "kavo brez mleka")
WRONG: "Mi gremo." when referring to exactly two people (use "Midva greva")
WRONG: "Što je to?" (Croatian — use "Kaj je to?")
