from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HotlineSeed:
    key: str
    query: str
    category_slug: str
    category_name: str


HOTLINE_SEEDS: tuple[HotlineSeed, ...] = (
    HotlineSeed(
        key="gaming-consoles",
        query="ігрова приставка",
        category_slug="gaming",
        category_name="Gaming",
    ),
    HotlineSeed(
        key="gaming-gamepads",
        query="gamepad",
        category_slug="gaming",
        category_name="Gaming",
    ),
    HotlineSeed(
        key="gaming-laptops",
        query="gaming laptop",
        category_slug="gaming",
        category_name="Gaming",
    ),
    HotlineSeed(
        key="electronics-headphones",
        query="wireless headphones",
        category_slug="electronics",
        category_name="Electronics",
    ),
    HotlineSeed(
        key="electronics-powerbanks",
        query="powerbank",
        category_slug="electronics",
        category_name="Electronics",
    ),
    HotlineSeed(
        key="home-coffee-machines",
        query="coffee machine",
        category_slug="home-kitchen",
        category_name="Home & Kitchen",
    ),
    HotlineSeed(
        key="toys-lego",
        query="lego",
        category_slug="toys-kids",
        category_name="Toys & Kids",
    ),
)


def get_hotline_seed(seed_key: str) -> HotlineSeed:
    for seed in HOTLINE_SEEDS:
        if seed.key == seed_key:
            return seed
    raise KeyError(seed_key)
