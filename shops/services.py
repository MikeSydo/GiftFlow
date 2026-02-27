"""
shops/services.py — Category matching service.

Provides CategoryMatcher: resolves a raw category string (from a scraper)
to a canonical gifts.Category using a synonym map + rapidfuzz fuzzy matching.
"""
import logging
import re
import unicodedata
from dataclasses import dataclass
from typing import Optional

from rapidfuzz import process, fuzz

logger = logging.getLogger('shops.services')

# Confidence threshold - tune as needed.
HIGH_CONFIDENCE_THRESHOLD = 85.0

# Synonym map: raw keyword (lowercase) -> category slug.
# The matcher normalises the input, then checks whether any key is a
# substring of the normalised string, and assigns full confidence (100.0).
# Extend this dict as you discover that scrapers use terms that fuzzy
# matching doesn't catch reliably.
SYNONYM_MAP: dict[str, str] = {
    # Electronics
    'laptop': 'electronics',
    'notebook': 'electronics',
    'smartphone': 'electronics',
    'phone': 'electronics',
    'tablet': 'electronics',
    'headphones': 'electronics',
    'speaker': 'electronics',

    # Home & Kitchen
    'kitchen': 'home',
    'cookware': 'home',
    'bedding': 'home',
    'furniture': 'home',
    'decor': 'home',

    # Sport & Outdoors
    'sport': 'sport',
    'fitness': 'sport',
    'outdoor': 'sport',
    'camping': 'sport',
    'bicycle': 'sport',

    # Beauty & Health
    'beauty': 'beauty',
    'cosmetic': 'beauty',
    'perfume': 'beauty',
    'skincare': 'beauty',

    # Books
    'book': 'books',
    'novel': 'books',

    # Toys & Kids
    'toy': 'toys',
    'kids': 'toys',
    'children': 'toys',
    'lego': 'toys',

    # Clothing
    'clothing': 'clothing',
    'apparel': 'clothing',
    'shoes': 'clothing',
    'dress': 'clothing',
    'jacket': 'clothing',

    # Food & Drinks
    'food': 'food',
    'wine': 'food',
    'chocolate': 'food',
    'coffee': 'food',

    # Experiences
    'experience': 'experiences',
    'voucher': 'experiences',
    'trip': 'experiences',
    'tour': 'experiences',
    'ticket': 'experiences',
}

@dataclass
class MatchResult:
    """Result of a category matching attempt."""
    category: object         # gifts.Category instance (or None)
    confidence: float        # 0.0–100.0
    needs_review: bool       # True if confidence is below threshold

def _normalise(text: str) -> str:
    """Lowercase, remove accents, strip non-alphanumeric characters."""
    # Unicode NFC normalisation
    text = unicodedata.normalize('NFKD', text)
    # Drop combining marks so accented chars become ASCII
    text = ''.join(c for c in text if not unicodedata.combining(c))
    text = text.lower()
    # Replace separators (slashes, dashes, commas) with space
    text = re.sub(r'[/\\\-,|>»›→]', ' ', text)
    # Remove remaining non-alphanumeric (keep spaces)
    text = re.sub(r'[^\w\s]', '', text)
    return ' '.join(text.split())   # collapse whitespace

class CategoryMatcher:
    """
    Matches a raw category string to a canonical gifts.Category.

    Usage::

        matcher = CategoryMatcher()
        result = matcher.match("Computers / Laptops")
        if result and not result.needs_review:
            product_link.gift.category = result.category
        else:
            product_link.needs_category_review = True

    Categories are loaded once and cached in a class-level variable.
    Call ``CategoryMatcher.invalidate_cache()`` after editing categories
    in Django Admin so the next call reloads from the DB.
    """

    # Class-level cache: list of (category_id, category_name, category_slug, category_obj)
    _cache: Optional[list[tuple]] = None

    def __init__(self, threshold: float = HIGH_CONFIDENCE_THRESHOLD):
        self.threshold = threshold

    @classmethod
    def _load_categories(cls) -> list[tuple]:
        """Return cached category data, loading from DB if necessary."""
        if cls._cache is None:
            cls._cache = cls._fetch_from_db()
        return cls._cache

    @classmethod
    def _fetch_from_db(cls) -> list[tuple]:
        from gifts.models import Category  # local import to avoid AppRegistryNotReady
        rows = []
        for cat in Category.objects.filter(is_active=True).only('id', 'name', 'slug'):
            rows.append((cat.id, cat.name, cat.slug, cat))
        logger.debug('CategoryMatcher loaded %d categories from DB', len(rows))
        return rows

    @classmethod
    def invalidate_cache(cls) -> None:
        """Clear the in-memory cache so the next call reloads from DB."""
        cls._cache = None
        logger.debug('CategoryMatcher cache invalidated')

    def match(self, raw: str) -> MatchResult:
        """
        Attempt to match *raw* (e.g. breadcrumbs from a shop) to a Category.

        Returns a MatchResult.  If no categories exist in the DB, returns a
        MatchResult with needs_review=True and confidence=0.
        """
        if not raw or not raw.strip():
            return MatchResult(category=None, confidence=0.0, needs_review=True)

        normalised = _normalise(raw)
        categories = self._load_categories()

        if not categories:
            logger.warning('CategoryMatcher: no active categories in DB')
            return MatchResult(category=None, confidence=0.0, needs_review=True)

        # Synonym map lookup (highest priority)
        for keyword, slug in SYNONYM_MAP.items():
            if keyword in normalised:
                matched = self._find_by_slug(categories, slug)
                if matched:
                    logger.debug(
                        'CategoryMatcher: synonym hit "%s" → slug="%s" (100.0)',
                        keyword, slug,
                    )
                    return MatchResult(category=matched, confidence=100.0, needs_review=False)
                # keyword found but slug not in our categories — fall through

        # Rapidfuzz match against category names
        choices = {row[1]: row[3] for row in categories}   # name → category obj
        best = process.extractOne(
            normalised,
            choices.keys(),
            scorer=fuzz.token_set_ratio,
            score_cutoff=0,
        )

        if best is None:
            return MatchResult(category=None, confidence=0.0, needs_review=True)

        matched_name, score, _ = best
        category_obj = choices[matched_name]
        needs_review = score < self.threshold

        logger.debug(
            'CategoryMatcher: fuzzy "%s" → "%s" score=%.1f needs_review=%s',
            raw, matched_name, score, needs_review,
        )
        return MatchResult(category=category_obj, confidence=score, needs_review=needs_review)

    @staticmethod
    def _find_by_slug(categories: list[tuple], slug: str):
        """Return category object matching *slug*, or None."""
        for _, _, cat_slug, cat_obj in categories:
            if cat_slug == slug:
                return cat_obj
        return None
