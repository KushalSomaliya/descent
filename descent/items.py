"""The nine items an agent can bring. Effects are applied in rules.py / engine.py."""

ITEMS: dict[str, str] = {
    "knife": "+2 attack strength when you attack.",
    "frying_pan": "+1 attack strength when you attack, +1 guard strength when you guard.",
    "helmet": "+2 guard strength when you guard.",
    "blanket": "Your hunger drops 0.6 less every night.",
    "big_spoon": "You eat 30% faster.",
    "first_aid_kit": "3 bandages for the whole game. Use one in your evening plan to heal up to 2 hearts of wounds "
                     "from attacks. It cannot heal hearts lost to hunger.",
    "megaphone": "If your floor-mate also has a megaphone, you each get 2 free talk messages every day.",
    "salt": "Bodies on your floor rot at 1 food per night instead of 2.",
    "book": "No game effect.",
}

ITEM_NAMES = list(ITEMS)


def normalize_item(raw: object) -> str | None:
    if not isinstance(raw, str):
        return None
    key = raw.strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {"pan": "frying_pan", "spoon": "big_spoon", "first_aid": "first_aid_kit", "medkit": "first_aid_kit"}
    key = aliases.get(key, key)
    return key if key in ITEMS else None
