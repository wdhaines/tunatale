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

**Case System Coverage — each lesson should naturally surface target nouns in ≥2–3 distinct cases:**

**Nominative** — subject, predicate after `biti`:
CORRECT: "To je dobra kava." — WRONG: "To je dobro kavo." (kavo is accusative)

**Genitive** — negation, quantities, `od/do/iz/brez/blizu`:
CORRECT: "Nimam časa." — WRONG: "Nimam čas." (čas is nominative)
CORRECT: "Kozarec vode, prosim." — WRONG: "Kozarec voda, prosim." (voda is nominative)

**Dative** — indirect object (no preposition), or `k/proti` only:
CORRECT: "Dam ti knjigo." — WRONG: "Dam ti knjiga." (knjiga is nominative)
NOTE: English "to" does NOT imply dative. `do` ("to/up to") governs the GENITIVE
("do gradu" = to the castle → genitive), and motion "to a place" with `v/na` is ACCUSATIVE
("v Ljubljano"). Pick case by the Slovene governing word, never by the English translation.

**Accusative** — direct object, motion-toward `v/na`:
CORRECT: "Grem v Ljubljano." — WRONG: "Grem v Ljubljana." (Ljubljana is nominative)
CORRECT: "Kavo, prosim." (accusative governed by prosim) — WRONG: "Kavno, prosim." (kavno is adjectival/neuter)
CORRECT: "Pivo, prosim." — WRONG: "Pivno, prosim."

**Locative** — location after `v/na/pri/o/po`:
CORRECT: "V Ljubljani je lepo." — WRONG: "V Ljubljana je lepo." (Ljubljana is nominative)

**Instrumental** — means/accompaniment with `z/s`:
CORRECT: "S prijateljem grem v kino." — WRONG: "S prijatelj grem v kino." (prijatelj is nominative)

Use target nouns in ≥2–3 distinct cases across each dialogue where natural — don't force unnatural case-stacking. The dual also participates: "z rokama" (dual instrumental), "brez rokavic" (plural genitive).

**Grammar — Critical patterns:**
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

**When vikanje is REQUIRED (use vi/vas/vam, not ti/te/ti):**
- Any service interaction: café, shop, hotel reception, information office, taxi
- Asking a stranger for directions
- Any scene where characters have not been introduced as friends/family
- Default: if in doubt, use vikanje

Vikanje verb forms for common verbs (A2):
- iti: Greste (not Greš)      "Greste po glavni cesti."
- biti: Ste (not si)           "Ste že bili v Ljubljani?"
- imeti: Imate (not imaš)     "Imate rezervacijo?"
- govoriti: Govorite (not govoriš)

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

**BEFORE OUTPUTTING — scan every Slovene word for:**
1. "izvinite" → replace with "oprostite"
2. "što" → replace with "kaj"
3. "nemoj" → replace with "ne" or "ne delaj"
4. "doviđenja" → replace with "nasvidenje"
5. "ć" or "đ" anywhere → these letters do not exist in Slovene
6. Any service-scene pronoun "ti/te/tvoj" → replace with "vi/vas/vaš"
7. Any "zanje/zanj" (for them/him) when addressing the listener → replace with "za vas"
