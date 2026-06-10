"""Plan mode management — plan file storage and state tracking."""

from __future__ import annotations

import os
import secrets
from typing import Optional

# Word lists for slug generation (adjective-verb-noun pattern), ported from
# cc/src/utils/words.ts so generated slugs read like cc's
# (e.g. 'gleaming-brewing-phoenix', 'cosmic-pondering-lighthouse').
_ADJECTIVES = [
    # Classic pleasant adjectives
    "abundant", "ancient", "bright", "calm", "cheerful", "clever", "cozy",
    "curious", "dapper", "dazzling", "deep", "delightful", "eager", "elegant",
    "enchanted", "fancy", "fluffy", "gentle", "gleaming", "golden", "graceful",
    "happy", "hidden", "humble", "jolly", "joyful", "keen", "kind", "lively",
    "lovely", "lucky", "luminous", "magical", "majestic", "mellow", "merry",
    "mighty", "misty", "noble", "peaceful", "playful", "polished", "precious",
    "proud", "quiet", "quirky", "radiant", "rosy", "serene", "shiny", "silly",
    "sleepy", "smooth", "snazzy", "snug", "snuggly", "soft", "sparkling",
    "spicy", "splendid", "sprightly", "starry", "steady", "sunny", "swift",
    "tender", "tidy", "toasty", "tranquil", "twinkly", "valiant", "vast",
    "velvet", "vivid", "warm", "whimsical", "wild", "wise", "witty", "wondrous",
    "zany", "zesty", "zippy",
    # Whimsical / magical
    "breezy", "bubbly", "buzzing", "cheeky", "cosmic", "crispy", "crystalline",
    "cuddly", "drifting", "dreamy", "effervescent", "ethereal", "fizzy",
    "flickering", "floating", "floofy", "fluttering", "foamy", "frolicking",
    "fuzzy", "giggly", "glimmering", "glistening", "glittery", "glowing",
    "goofy", "groovy", "harmonic", "hazy", "humming", "iridescent", "jaunty",
    "jazzy", "jiggly", "melodic", "moonlit", "mossy", "nifty", "peppy",
    "prancy", "purrfect", "purring", "quizzical", "rippling", "rustling",
    "shimmering", "shimmying", "snappy", "snoopy", "squishy", "swirling",
    "ticklish", "tingly", "twinkling", "velvety", "wiggly", "wobbly", "woolly",
    "zazzy",
    # Programming concepts
    "abstract", "adaptive", "agile", "async", "atomic", "binary", "cached",
    "compiled", "composed", "compressed", "concurrent", "cryptic", "curried",
    "declarative", "delegated", "distributed", "dynamic", "encapsulated",
    "enumerated", "eventual", "expressive", "federated", "functional",
    "generic", "greedy", "hashed", "idempotent", "immutable", "imperative",
    "indexed", "inherited", "iterative", "lazy", "lexical", "linear", "linked",
    "logical", "memoized", "modular", "mutable", "nested", "optimized",
    "parallel", "parsed", "partitioned", "piped", "polymorphic", "pure",
    "reactive", "recursive", "refactored", "reflective", "replicated",
    "resilient", "robust", "scalable", "sequential", "serialized", "sharded",
    "sorted", "staged", "stateful", "stateless", "streamed", "structured",
    "synchronous", "synthetic", "temporal", "transient", "typed", "unified",
    "validated", "vectorized", "virtual",
]

# Verbs for the middle word — whimsical action words.
_VERBS = [
    "baking", "beaming", "booping", "bouncing", "brewing", "bubbling",
    "chasing", "churning", "coalescing", "conjuring", "cooking", "crafting",
    "crunching", "cuddling", "dancing", "dazzling", "discovering", "doodling",
    "dreaming", "drifting", "enchanting", "exploring", "finding", "floating",
    "fluttering", "foraging", "forging", "frolicking", "gathering", "giggling",
    "gliding", "greeting", "growing", "hatching", "herding", "honking",
    "hopping", "hugging", "humming", "imagining", "inventing", "jingling",
    "juggling", "jumping", "kindling", "knitting", "launching", "leaping",
    "mapping", "marinating", "meandering", "mixing", "moseying", "munching",
    "napping", "nibbling", "noodling", "orbiting", "painting", "percolating",
    "petting", "plotting", "pondering", "popping", "prancing", "purring",
    "puzzling", "questing", "riding", "roaming", "rolling", "sauteeing",
    "scribbling", "seeking", "shimmying", "singing", "skipping", "sleeping",
    "snacking", "sniffing", "snuggling", "soaring", "sparking", "spinning",
    "splashing", "sprouting", "squishing", "stargazing", "stirring",
    "strolling", "swimming", "swinging", "tickling", "tinkering", "toasting",
    "tumbling", "twirling", "waddling", "wandering", "watching", "weaving",
    "whistling", "wibbling", "wiggling", "wishing", "wobbling", "wondering",
    "yawning", "zooming",
]

_NOUNS = [
    # Nature & cosmic
    "aurora", "avalanche", "blossom", "breeze", "brook", "bubble", "canyon",
    "cascade", "cloud", "clover", "comet", "coral", "cosmos", "creek",
    "crescent", "crystal", "dawn", "dewdrop", "dusk", "eclipse", "ember",
    "feather", "fern", "firefly", "flame", "flurry", "fog", "forest", "frost",
    "galaxy", "garden", "glacier", "glade", "grove", "harbor", "horizon",
    "island", "lagoon", "lake", "leaf", "lightning", "meadow", "meteor",
    "mist", "moon", "moonbeam", "mountain", "nebula", "nova", "ocean", "orbit",
    "pebble", "petal", "pine", "planet", "pond", "puddle", "quasar", "rain",
    "rainbow", "reef", "ripple", "river", "shore", "sky", "snowflake", "spark",
    "spring", "star", "stardust", "starlight", "storm", "stream", "summit",
    "sun", "sunbeam", "sunrise", "sunset", "thunder", "tide", "twilight",
    "valley", "volcano", "waterfall", "wave", "willow", "wind",
    # Cute creatures
    "alpaca", "axolotl", "badger", "bear", "beaver", "bee", "bird", "bumblebee",
    "bunny", "cat", "chipmunk", "crab", "crane", "deer", "dolphin", "dove",
    "dragon", "dragonfly", "duckling", "eagle", "elephant", "falcon", "finch",
    "flamingo", "fox", "frog", "giraffe", "goose", "hamster", "hare",
    "hedgehog", "hippo", "hummingbird", "jellyfish", "kitten", "koala",
    "ladybug", "lark", "lemur", "llama", "lobster", "lynx", "manatee",
    "meerkat", "moth", "narwhal", "newt", "octopus", "otter", "owl", "panda",
    "parrot", "peacock", "pelican", "penguin", "phoenix", "piglet", "platypus",
    "pony", "porcupine", "puffin", "puppy", "quail", "quokka", "rabbit",
    "raccoon", "raven", "robin", "salamander", "seahorse", "seal", "sloth",
    "snail", "sparrow", "sphinx", "squid", "squirrel", "starfish", "swan",
    "tiger", "toucan", "turtle", "unicorn", "walrus", "whale", "wolf",
    "wombat", "wren", "yeti", "zebra",
    # Fun objects & concepts
    "acorn", "anchor", "balloon", "beacon", "biscuit", "blanket", "bonbon",
    "book", "boot", "cake", "candle", "candy", "castle", "charm", "clock",
    "cocoa", "cookie", "crayon", "crown", "cupcake", "donut", "dream", "fairy",
    "fiddle", "flask", "flute", "fountain", "gadget", "gem", "gizmo", "globe",
    "goblet", "hammock", "harp", "haven", "hearth", "honey", "journal",
    "kazoo", "kettle", "key", "kite", "lantern", "lemon", "lighthouse",
    "locket", "lollipop", "mango", "map", "marble", "marshmallow", "melody",
    "mitten", "mochi", "muffin", "music", "nest", "noodle", "oasis", "origami",
    "pancake", "parasol", "peach", "pearl", "pie", "pillow", "pinwheel",
    "pixel", "pizza", "plum", "popcorn", "pretzel", "prism", "pudding",
    "pumpkin", "puzzle", "quiche", "quill", "quilt", "riddle", "rocket",
    "rose", "scone", "scroll", "shell", "sketch", "snowglobe", "sonnet",
    "sparkle", "spindle", "sprout", "sundae", "swing", "taco", "teacup",
    "teapot", "thimble", "toast", "token", "tome", "tower", "treasure",
    "treehouse", "trinket", "truffle", "tulip", "umbrella", "waffle", "wand",
    "whisper", "whistle", "widget", "wreath", "zephyr",
]


def generate_word_slug() -> str:
    """Generate a random adjective-verb-noun slug (mirror cc generateWordSlug).

    Example: 'gleaming-brewing-phoenix', 'cosmic-pondering-lighthouse'.
    """
    adjective = secrets.choice(_ADJECTIVES)
    verb = secrets.choice(_VERBS)
    noun = secrets.choice(_NOUNS)
    return f"{adjective}-{verb}-{noun}"


def _resolve_plans_dir(config_home: str, plans_directory: str | None) -> str:
    """Resolve the plans directory honoring the cc ``plansDirectory`` setting.

    If ``plans_directory`` is set it is resolved relative to the project root
    (cwd) and validated to stay within it (path-traversal guard); if it
    escapes, fall back to ``<config_home>/plans``. Otherwise default to
    ``<config_home>/plans`` (mirror cc getPlansDirectory).
    """
    default_dir = os.path.join(config_home, "plans")
    if not plans_directory:
        return default_dir

    cwd = os.path.abspath(os.getcwd())
    resolved = os.path.abspath(os.path.join(cwd, plans_directory))
    # Validate path stays within project root to prevent path traversal.
    if resolved == cwd or resolved.startswith(cwd + os.sep):
        return resolved
    return default_dir


class PlanManager:
    """Manages plan files and plan mode state."""

    def __init__(
        self,
        config_home: str | None = None,
        plans_directory: str | None = None,
    ):
        if config_home is None:
            config_home = os.path.join(os.path.expanduser("~"), ".ccos")
        self._plans_dir = _resolve_plans_dir(config_home, plans_directory)
        os.makedirs(self._plans_dir, exist_ok=True)
        # Session -> slug mapping
        self._slug_cache: dict[str, str] = {}
        # Current plan mode state
        self.is_plan_mode: bool = False
        self._pre_plan_mode: str | None = None  # permission mode before plan

    @property
    def plans_dir(self) -> str:
        return self._plans_dir

    def get_slug(self, session_id: str) -> str:
        """Get or generate a slug for this session."""
        if session_id not in self._slug_cache:
            # Try to find a unique slug
            for _ in range(10):
                slug = generate_word_slug()
                path = os.path.join(self._plans_dir, f"{slug}.md")
                if not os.path.exists(path):
                    break
            self._slug_cache[session_id] = slug
        return self._slug_cache[session_id]

    def set_slug(self, session_id: str, slug: str) -> None:
        self._slug_cache[session_id] = slug

    def clear_slug(self, session_id: str) -> None:
        self._slug_cache.pop(session_id, None)

    def get_plan_file_path(self, session_id: str, agent_id: str | None = None) -> str:
        slug = self.get_slug(session_id)
        if agent_id:
            return os.path.join(self._plans_dir, f"{slug}-agent-{agent_id}.md")
        return os.path.join(self._plans_dir, f"{slug}.md")

    def get_plan(self, session_id: str, agent_id: str | None = None) -> str | None:
        """Read the plan file content."""
        path = self.get_plan_file_path(session_id, agent_id)
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except FileNotFoundError:
            return None
        except Exception:
            return None

    def save_plan(self, session_id: str, content: str, agent_id: str | None = None) -> str:
        """Write the plan to disk. Returns the file path."""
        path = self.get_plan_file_path(session_id, agent_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
        return path

    def enter_plan_mode(self, current_perm_mode: str) -> None:
        """Enter plan mode, saving the current permission mode."""
        self._pre_plan_mode = current_perm_mode
        self.is_plan_mode = True

    def exit_plan_mode(self) -> str:
        """Exit plan mode, returning the original permission mode."""
        mode = self._pre_plan_mode or "default"
        self._pre_plan_mode = None
        self.is_plan_mode = False
        return mode
