from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HotlineSeedTemplate:
    key: str
    query: str
    category_slug: str
    category_name: str
    source_url: str = ""
    parent_slug: str = ""
    parent_name: str = ""
    priority: int = 100


HOTLINE_SEED_TEMPLATES: tuple[HotlineSeedTemplate, ...] = (
    HotlineSeedTemplate("gaming-consoles", "", "gaming-consoles", "Gaming consoles", "https://hotline.ua/ua/computer/igrovye-pristavki/", "gaming", "Gaming", 10),
    HotlineSeedTemplate("gaming-handheld-consoles", "", "gaming-handheld-consoles", "Handheld consoles", "https://hotline.ua/ua/computer/portativnye-igrovye-pristavki/", "gaming", "Gaming", 20),
    HotlineSeedTemplate("gaming-gamepads", "", "gaming-gamepads", "Gamepads and controllers", "https://hotline.ua/ua/computer/gejmpady-dzhojstiki-ruli/", "gaming", "Gaming", 30),
    HotlineSeedTemplate("gaming-headsets", "gaming headset", "gaming-headsets", "Gaming headsets", "", "gaming", "Gaming", 40),
    HotlineSeedTemplate("gaming-mice", "gaming mouse", "gaming-mice", "Gaming mice", "", "gaming", "Gaming", 50),
    HotlineSeedTemplate("gaming-keyboards", "gaming keyboard", "gaming-keyboards", "Gaming keyboards", "", "gaming", "Gaming", 60),
    HotlineSeedTemplate("electronics-smartphones", "smartphone", "electronics-smartphones", "Smartphones", "", "electronics", "Electronics", 10),
    HotlineSeedTemplate("electronics-smartwatches", "smart watch", "electronics-smartwatches", "Smart watches", "", "electronics", "Electronics", 20),
    HotlineSeedTemplate("electronics-tablets", "tablet", "electronics-tablets", "Tablets", "", "electronics", "Electronics", 30),
    HotlineSeedTemplate("electronics-headphones", "wireless headphones", "electronics-headphones", "Headphones", "", "electronics", "Electronics", 40),
    HotlineSeedTemplate("electronics-powerbanks", "powerbank", "electronics-powerbanks", "Power banks", "", "electronics", "Electronics", 50),
    HotlineSeedTemplate("home-coffee-machines", "coffee machine", "home-coffee-machines", "Coffee machines", "", "home-kitchen", "Home & Kitchen", 10),
    HotlineSeedTemplate("home-robot-vacuums", "robot vacuum", "home-robot-vacuums", "Robot vacuums", "", "home-kitchen", "Home & Kitchen", 20),
    HotlineSeedTemplate("home-air-fryers", "air fryer", "home-air-fryers", "Air fryers", "", "home-kitchen", "Home & Kitchen", 30),
    HotlineSeedTemplate("beauty-hair-dryers", "hair dryer", "beauty-hair-dryers", "Hair dryers", "", "beauty", "Beauty", 10),
    HotlineSeedTemplate("beauty-shavers", "electric shaver", "beauty-shavers", "Electric shavers", "", "beauty", "Beauty", 20),
    HotlineSeedTemplate("fashion-backpacks", "backpack", "fashion-backpacks", "Backpacks", "", "fashion", "Fashion", 10),
    HotlineSeedTemplate("sports-dumbbells", "dumbbells", "sports-dumbbells", "Dumbbells", "", "sports-outdoors", "Sports & Outdoors", 10),
    HotlineSeedTemplate("auto-dash-cams", "dash cam", "auto-dash-cams", "Dash cams", "", "auto", "Auto", 10),
    HotlineSeedTemplate("toys-lego", "lego", "toys-lego", "LEGO", "", "toys-kids", "Toys & Kids", 10),
    HotlineSeedTemplate("office-chairs", "office chair", "office-chairs", "Office chairs", "", "office", "Office", 10),
    HotlineSeedTemplate("travel-suitcases", "suitcase", "travel-suitcases", "Suitcases", "", "travel", "Travel", 10),
)

# Backward-compatible name for tests and template import code. Runtime ingestion
# reads active HotlineSeed rows from the database.
HotlineSeed = HotlineSeedTemplate
HOTLINE_SEEDS = HOTLINE_SEED_TEMPLATES


def get_hotline_seed_template(seed_key: str) -> HotlineSeedTemplate:
    for seed in HOTLINE_SEED_TEMPLATES:
        if seed.key == seed_key:
            return seed
    raise KeyError(seed_key)
