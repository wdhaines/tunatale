"""Pixabay image fetcher with ranked result selection."""

from __future__ import annotations

import math
import re

import httpx

_PIXABAY_API = "https://pixabay.com/api/"

QUERY_MAP: dict[str, str] = {
    # Animals
    "wing": "bird wing",
    "animal": "dog cat cow farm animals",
    # Transportation
    "tire": "car tire wheel",
    "(train) ticket": "train ticket",
    "transportation": "bus train car transportation",
    # Location
    "court": "courtroom interior",
    "camp": "camping tent",
    "location": "map location pin",
    "space (outer)": "outer space galaxy",
    "ground": "dirt ground soil",
    # Clothing
    "suit": "business suit man",
    "clothing": "clothes wardrobe",
    # People / society
    "fan": "sports fan crowd cheering",
    "fan (electric)": "electric fan appliance",
    "adult": "adult man woman",
    "human": "human person silhouette",
    "crowd": "crowd of people",
    "person": "person portrait",
    "parent": "mother father family",
    "child": "child playing",
    # Society
    "bill": "invoice receipt paper",
    "marriage": "wedding rings marriage",
    "wedding": "wedding ceremony",
    "race (ethnicity)": "diverse people multicultural",
    "race (sport)": "running race competition",
    "sex (act)": "romance couple",
    "sex (gender)": "gender male female symbol",
    "drug": "medicine pills pharmacy",
    "sign": "road sign",
    "election": "voting ballot box",
    "gun": "pistol firearm",
    "prison": "prison bars cell",
    "exercise": "exercise workout gym",
    "contract": "contract paper signing",
    "energy": "energy lightning bolt",
    "attack": "attack combat",
    "God": "church cross religion",
    # Art
    "instrument (musical)": "musical instrument",
    "band": "music band concert",
    "art": "painting artwork canvas",
    # Food
    "orange (fruit)": "orange fruit",
    "chicken": "chicken meat food",
    "oil": "cooking oil bottle",
    "breakfast": "breakfast food morning",
    "lunch": "lunch meal midday",
    "dinner": "dinner meal table",
    "food": "food meal variety",
    "beverage": "drinks beverages glasses",
    # Home
    "dream": "dream clouds sleep",
    "key": "key lock",
    "paint": "paint brush painting",
    "letter": "letter envelope mail",
    "note": "note paper writing",
    "tool": "tools hammer wrench",
    "pen": "ballpoint pen writing",
    "lock": "padlock locked",
    "gift": "gift box present ribbon",
    "ring": "ring jewelry",
    "card": "greeting card",
    "pool": "swimming pool",
    "wall": "brick wall interior",
    "floor": "floor tile wood",
    "ceiling": "ceiling interior room",
    "roof": "house roof",
    # Electronics
    "screen": "computer screen monitor",
    "program (computer)": "computer program code",
    "network": "computer network",
    "camera": "camera photo",
    "television": "television TV set",
    # Body
    "back": "human back body",
    "tear (drop)": "teardrop crying eye",
    "tongue": "tongue mouth",
    "toe": "toes foot",
    "sweat": "sweat exercise running",
    "skin": "skin texture close up",
    "voice": "microphone singing voice",
    "disease": "sick person illness",
    # Nature
    "sea": "sea ocean waves coast",
    "ocean": "ocean deep blue water",
    "soil/earth": "soil dirt earth",
    "flower": "flower bloom colorful",
    "root": "tree root ground",
    "wave": "ocean wave surf",
    "heat": "heat sun hot desert",
    "nature": "nature landscape green",
    # Materials
    "material": "fabric material texture",
    "glass": "glass drinking transparent",
    "wood": "wood texture plank",
    "stone": "stone rock",
    "clay": "clay pottery",
    "dust": "dust particles sunlight",
    "gold": "gold bars shiny",
    "copper": "copper metal wire",
    "silver": "silver metal coins",
    # Math / measurements
    "foot (unit)": "ruler measuring foot",
    "inch": "ruler inch measuring",
    "pound": "scale weight pound",
    "half": "half apple cut",
    "circle": "circle shape geometry",
    "square": "square shape geometry",
    "temperature": "thermometer temperature",
    "weight": "scale weight balance",
    "edge": "edge cliff ledge",
    "corner": "corner wall room",
    "date": "calendar date",
    # Misc nouns
    "light": "sunlight bright lamp",
    "sound": "sound wave speaker",
    "yes": "thumbs up yes agree",
    "no": "thumbs down no refuse",
    "piece": "puzzle piece",
    "pain": "pain ouch injury",
    "hole": "hole ground pit",
    "image": "photograph picture frame",
    "pattern": "geometric pattern texture",
    "dot": "dots pattern circle",
    "consonant": "alphabet letters consonant",
    "vowel": "vowels letters A E I",
    "noun": "grammar noun word",
    "verb": "grammar verb action",
    "adjective": "grammar adjective word",
    # Directions
    "top": "top mountain summit",
    "bottom": "bottom floor lowest",
    "side": "side profile face",
    "front": "front door building",
    "back (direction)": "back behind arrow",
    "outside": "outside outdoor nature",
    "inside": "inside interior room",
    "up": "arrow up direction",
    "down": "arrow down direction",
    "left": "left arrow direction",
    "right": "right arrow direction",
    "straight": "straight road highway",
    "north": "north compass direction",
    "south": "south compass direction",
    "east": "east compass direction",
    "west": "west sunset compass",
    "direction": "compass direction navigation",
    # Seasons
    "season": "four seasons spring summer autumn winter",
    "Fall/Autumn": "autumn fall leaves",
    # Numbers
    "1": "number 1 one sign",
    "2": "number 2 two sign",
    "3": "number 3 three sign",
    "4": "number 4 four sign",
    "5": "number 5 five sign",
    "6": "number 6 six sign",
    "7": "number 7 seven sign",
    "8": "number 8 eight sign",
    "9": "number 9 nine sign",
    "10": "number 10 ten sign",
    "11": "11 number sign",
    "12": "12 number sign",
    "13": "13 number sign",
    "14": "14 number sign",
    "15": "15 number sign",
    "16": "16 number sign",
    "17": "17 number sign",
    "18": "18 number sign",
    "19": "19 number sign",
    "20": "20 number sign",
    "21": "21 number sign",
    "22": "22 number sign",
    "30": "30 number sign",
    "31": "31 number sign",
    "32": "32 number sign",
    "40": "40 number sign",
    "41": "41 number sign",
    "42": "42 number sign",
    "50": "50 number sign",
    "51": "51 number sign",
    "52": "52 number sign",
    "60": "60 number sign",
    "61": "61 number sign",
    "62": "62 number sign",
    "70": "70 number sign",
    "71": "71 number sign",
    "72": "72 number sign",
    "80": "80 number sign",
    "81": "81 number sign",
    "82": "82 number sign",
    "90": "90 number sign",
    "91": "91 number sign",
    "92": "92 number sign",
    "100": "100 number sign",
    "101": "101 number address",
    "102": "102 number address",
    "110": "110 number sign",
    "111": "111 number sign",
    "1000": "1000 thousand number",
    "1001": "1001 number",
    "10000": "10000 number",
    "100000": "100000 number",
    "million": "million money cash",
    "billion": "billion money finance",
    "1st": "first place gold medal",
    "2nd": "second place silver medal",
    "3rd": "third place bronze medal",
    "4th": "4th number podium",
    "5th": "5th number podium",
    "number": "numbers counting",
    # Verbs
    "work": "working office desk",
    "play": "children playing",
    "walk": "walking path person",
    "run": "running athlete",
    "drive": "driving car steering wheel",
    "fly": "flying airplane sky",
    "swim": "swimming pool water",
    "go": "walking going forward",
    "stop": "stop sign hand",
    "follow": "following footsteps path",
    "think": "thinking person head",
    "speak": "speaking talking microphone",
    "say": "speaking mouth talking",
    "eat": "eating food fork",
    "drink": "drinking glass water",
    "kill": "danger warning skull",
    "die": "gravestone cemetery death",
    "smile": "smiling face happy",
    "laugh": "laughing person happy",
    "cry": "crying tears face",
    "buy": "shopping bag purchase",
    "pay": "paying cash card",
    "sell": "market selling vendor",
    "shoot (a gun)": "shooting target gun range",
    "learn": "studying books learning",
    "jump": "jumping leap athlete",
    "smell": "smelling flower nose",
    "hear": "ear listening sound",
    "listen": "headphones listening music",
    "taste": "tasting food tongue",
    "touch": "touching hand finger",
    "see": "eye vision looking",
    "watch": "watching screen television",
    "kiss": "kissing couple lips",
    "burn": "fire burning flame",
    "melt": "melting ice cream sun",
    "dig": "digging shovel earth",
    "explode": "explosion blast fire",
    "sit": "sitting chair person",
    "stand": "standing person upright",
    "love": "love heart couple",
    "pass by": "passing walking by",
    "cut": "cutting knife food",
    "fight": "fighting boxing match",
    "lie down": "lying down rest bed",
    "dance": "dancing couple ballroom",
    "sleep": "sleeping bed night",
    "wake up": "wake up morning alarm",
    "sing": "singing microphone concert",
    "count": "counting numbers fingers",
    "marry": "wedding marriage couple",
    "pray": "praying hands church",
    "win": "winning trophy celebration",
    "lose": "losing sad defeat",
    "mix/stir": "stirring mixing bowl cooking",
    "bend": "bending flexible yoga",
    "wash": "washing hands water soap",
    "cook": "cooking kitchen chef",
    "open": "opening door handle",
    "close": "closing door shut",
    "write": "writing pen paper",
    "call": "phone call talking",
    "turn": "turning corner road",
    "build": "building construction worker",
    "teach": "teaching classroom teacher",
    "grow": "growing plant sprout",
    "draw": "drawing pencil sketch",
    "feed": "feeding animals bird",
    "catch": "catching ball hands",
    "throw": "throwing ball athlete",
    "clean": "cleaning cloth surface",
    "find": "finding searching magnifying glass",
    "fall": "falling autumn leaves",
    "push": "pushing effort force",
    "pull": "pulling rope tug",
    "carry": "carrying box lifting",
    "break": "broken smashed glass",
    "wear": "wearing clothes fashion",
    "hang": "hanging picture wall",
    "shake": "handshake hands greeting",
    "sign (verb)": "signing document pen",
    "beat": "heartbeat pulse rhythm",
    "lift": "lifting weights gym",
    # Adjectives
    "long": "long road highway",
    "short (≠long)": "short ruler measuring",
    "tall": "tall skyscraper building",
    "short (≠tall)": "short child small person",
    "wide": "wide road highway",
    "narrow": "narrow alley street",
    "big/large": "large big elephant",
    "small/little": "small tiny mouse",
    "slow": "slow snail tortoise",
    "fast": "fast cheetah speed",
    "hot": "hot fire sun desert",
    "cold": "cold ice snow winter",
    "warm": "warm cozy fireplace",
    "cool": "cool breeze fan shade",
    "new": "new shiny product",
    "old (≠new)": "old worn rusty object",
    "young": "young child baby",
    "old (≠young)": "elderly old person",
    "good": "thumbs up good quality",
    "bad": "thumbs down bad broken",
    "wet": "wet rain water drops",
    "dry": "dry desert cracked earth",
    "sick": "sick ill person bed",
    "healthy": "healthy fit exercise",
    "loud": "loud speaker concert music",
    "quiet": "quiet peaceful calm nature",
    "happy": "happy smiling face",
    "sad": "sad crying unhappy",
    "beautiful": "beautiful landscape scenery",
    "ugly": "ugly broken abandoned",
    "deaf": "sign language hands",
    "blind": "blind cane walking",
    "nice": "friendly smiling kind",
    "mean": "angry face argument",
    "rich": "rich luxury mansion money",
    "poor": "poor homeless poverty",
    "thick": "thick book stack",
    "thin": "thin paper sheet",
    "expensive": "expensive luxury jewelry",
    "cheap": "sale discount price tag",
    "flat": "flat surface table top",
    "curved": "curved arch bridge",
    "male": "male man symbol",
    "female": "female woman symbol",
    "tight": "tight fit squeeze",
    "loose": "loose clothing baggy",
    "high": "high altitude mountain",
    "low": "low ground flat",
    "soft": "soft pillow fluffy",
    "hard": "hard rock stone",
    "deep": "deep ocean underwater",
    "shallow": "shallow water stream",
    "dirty": "dirty mud stain",
    "strong": "strong muscle athlete",
    "weak": "weak tired exhausted",
    "dead": "dead dry plant wilted",
    "alive": "alive green plant nature",
    "heavy": "heavy weight barbell",
    "light (≠heavy)": "light feather floating",
    "dark": "dark night shadow",
    "light (≠dark)": "bright sunlight daylight",
    "nuclear": "nuclear power plant atom",
    "famous": "famous star spotlight crowd",
    # Pronouns
    "I": "person pointing at self",
    "you (sg informal)": "person pointing at you",
    "he": "man pointing",
    "she": "woman portrait",
    "it": "question mark object",
    "we": "group people together",
    "you (plural/formal)": "group people pointing",
    "they": "group people diverse",
}


def build_query(english: str) -> str:
    """Return best Pixabay search query for an English word."""
    if english in QUERY_MAP:
        return QUERY_MAP[english]
    return re.sub(r"\s*\(.*?\)", "", english).strip()


def _tag_overlap(query_tokens: frozenset[str], tags_str: str) -> float:
    tag_words = {t.strip().lower() for t in tags_str.split(",") if t.strip()}
    return float(len(query_tokens & tag_words))


def score_hit(hit: dict, query_tokens: frozenset[str]) -> float:
    """Rank a Pixabay hit by engagement and query relevance."""
    likes = hit.get("likes", 0) or 0
    views = hit.get("views", 0) or 0
    tags = hit.get("tags", "") or ""
    return 0.5 * math.log(likes + 1) + 0.3 * math.log(views + 1) + _tag_overlap(query_tokens, tags)


def best_hit(hits: list[dict], query: str) -> dict | None:
    """Return highest-scoring hit, preferring photos over illustrations."""
    if not hits:
        return None
    photo_hits = [h for h in hits if h.get("imageType") == "photo" or h.get("type") == "photo"]
    candidates = photo_hits if photo_hits else hits
    tokens = frozenset(query.lower().split())
    return max(candidates, key=lambda h: score_hit(h, tokens))


def fetch_pixabay_image(
    english: str,
    *,
    api_key: str,
    http_client: httpx.Client | None = None,
    used_urls: frozenset[str] = frozenset(),
) -> tuple[bytes, str, str] | None:
    """Fetch best-ranked Pixabay image. Returns (image_bytes, ext, url) or None.

    Hits whose webformatURL is in used_urls are excluded before ranking.
    """
    query = build_query(english)
    owned = http_client is None
    client = http_client or httpx.Client()
    try:
        resp = client.get(
            _PIXABAY_API,
            params={
                "key": api_key,
                "q": query,
                "image_type": "photo",
                "safesearch": "true",
                "per_page": 20,
                "min_width": 300,
            },
            timeout=10,
        )
        resp.raise_for_status()
        hits = resp.json().get("hits", [])
        available = [h for h in hits if h.get("webformatURL", "") not in used_urls]
        hit = best_hit(available, query)
        if hit is None:
            return None
        img_url = hit.get("webformatURL", "")
        if not img_url:
            return None
        r = client.get(img_url, timeout=15)
        r.raise_for_status()
        ext = "jpg" if "jpg" in img_url.lower() else "png"
        return r.content, ext, img_url
    except Exception:
        return None
    finally:
        if owned:
            client.close()
