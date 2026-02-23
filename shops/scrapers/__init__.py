"""
Scraper registry — maps shop slugs to scraper classes.
"""
from .rozetka import RozetkaSpider

SCRAPER_REGISTRY: dict[str, type] = {
    'rozetka': RozetkaSpider,
}


def get_scraper(shop_slug: str):
    """Return an instantiated scraper for the given shop slug, or None."""
    cls = SCRAPER_REGISTRY.get(shop_slug)
    if cls is None:
        return None
    return cls()
