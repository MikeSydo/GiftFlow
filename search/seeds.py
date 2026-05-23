from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HotlineSeedTemplate:
    key: str
    query: str
    category_slug: str
    category_name: str
    priority: int = 100


HOTLINE_SEED_TEMPLATES: tuple[HotlineSeedTemplate, ...] = (
    HotlineSeedTemplate("gaming-playstation", "playstation 5", "gaming", "Gaming", 10),
    HotlineSeedTemplate("gaming-xbox", "xbox series", "gaming", "Gaming", 20),
    HotlineSeedTemplate("gaming-nintendo", "nintendo switch", "gaming", "Gaming", 30),
    HotlineSeedTemplate("gaming-gamepads", "gamepad", "gaming", "Gaming", 40),
    HotlineSeedTemplate("gaming-laptops", "gaming laptop", "gaming", "Gaming", 50),
    HotlineSeedTemplate("gaming-mice", "gaming mouse", "gaming", "Gaming", 60),
    HotlineSeedTemplate("gaming-headsets", "gaming headset", "gaming", "Gaming", 70),
    HotlineSeedTemplate("electronics-smartphones", "smartphone", "electronics", "Electronics", 10),
    HotlineSeedTemplate("electronics-smartwatches", "smart watch", "electronics", "Electronics", 20),
    HotlineSeedTemplate("electronics-tablets", "tablet", "electronics", "Electronics", 30),
    HotlineSeedTemplate("electronics-headphones", "wireless headphones", "electronics", "Electronics", 40),
    HotlineSeedTemplate("electronics-powerbanks", "powerbank", "electronics", "Electronics", 50),
    HotlineSeedTemplate("electronics-speakers", "bluetooth speaker", "electronics", "Electronics", 60),
    HotlineSeedTemplate("electronics-cameras", "action camera", "electronics", "Electronics", 70),
    HotlineSeedTemplate("home-coffee-machines", "coffee machine", "home-kitchen", "Home & Kitchen", 10),
    HotlineSeedTemplate("home-robot-vacuums", "robot vacuum", "home-kitchen", "Home & Kitchen", 20),
    HotlineSeedTemplate("home-air-fryers", "air fryer", "home-kitchen", "Home & Kitchen", 30),
    HotlineSeedTemplate("home-blenders", "blender", "home-kitchen", "Home & Kitchen", 40),
    HotlineSeedTemplate("home-multicookers", "multicooker", "home-kitchen", "Home & Kitchen", 50),
    HotlineSeedTemplate("beauty-hair-dryers", "hair dryer", "beauty", "Beauty", 10),
    HotlineSeedTemplate("beauty-hair-stylers", "hair styler", "beauty", "Beauty", 20),
    HotlineSeedTemplate("beauty-shavers", "electric shaver", "beauty", "Beauty", 30),
    HotlineSeedTemplate("fashion-backpacks", "backpack", "fashion", "Fashion", 10),
    HotlineSeedTemplate("fashion-wallets", "wallet", "fashion", "Fashion", 20),
    HotlineSeedTemplate("accessories-sunglasses", "sunglasses", "accessories", "Accessories", 10),
    HotlineSeedTemplate("sports-fitness-bracelets", "fitness bracelet", "sports-outdoors", "Sports & Outdoors", 10),
    HotlineSeedTemplate("sports-dumbbells", "dumbbells", "sports-outdoors", "Sports & Outdoors", 20),
    HotlineSeedTemplate("sports-scooters", "scooter", "sports-outdoors", "Sports & Outdoors", 30),
    HotlineSeedTemplate("auto-dash-cams", "dash cam", "auto", "Auto", 10),
    HotlineSeedTemplate("auto-car-vacuums", "car vacuum", "auto", "Auto", 20),
    HotlineSeedTemplate("auto-compressors", "car compressor", "auto", "Auto", 30),
    HotlineSeedTemplate("toys-lego", "lego", "toys-kids", "Toys & Kids", 10),
    HotlineSeedTemplate("toys-board-games", "board game", "toys-kids", "Toys & Kids", 20),
    HotlineSeedTemplate("toys-rc-cars", "radio controlled car", "toys-kids", "Toys & Kids", 30),
    HotlineSeedTemplate("hobbies-drawing-tablets", "drawing tablet", "hobbies-creativity", "Hobbies & Creativity", 10),
    HotlineSeedTemplate("hobbies-3d-pens", "3d pen", "hobbies-creativity", "Hobbies & Creativity", 20),
    HotlineSeedTemplate("hobbies-sewing-machines", "sewing machine", "hobbies-creativity", "Hobbies & Creativity", 30),
    HotlineSeedTemplate("pets-feeders", "pet feeder", "pets", "Pets", 10),
    HotlineSeedTemplate("pets-fountains", "cat fountain", "pets", "Pets", 20),
    HotlineSeedTemplate("office-chairs", "office chair", "office", "Office", 10),
    HotlineSeedTemplate("office-desk-lamps", "desk lamp", "office", "Office", 20),
    HotlineSeedTemplate("office-printers", "printer", "office", "Office", 30),
    HotlineSeedTemplate("travel-suitcases", "suitcase", "travel", "Travel", 10),
    HotlineSeedTemplate("travel-thermoses", "thermos", "travel", "Travel", 20),
)

# Backward-compatible name for tests and template import code. Runtime ingestion
# reads active search.HotlineSeed rows from the database.
HotlineSeed = HotlineSeedTemplate
HOTLINE_SEEDS = HOTLINE_SEED_TEMPLATES


def get_hotline_seed_template(seed_key: str) -> HotlineSeedTemplate:
    for seed in HOTLINE_SEED_TEMPLATES:
        if seed.key == seed_key:
            return seed
    raise KeyError(seed_key)
