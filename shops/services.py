import logging
import re
import unicodedata
from dataclasses import dataclass
from typing import Optional

from rapidfuzz import fuzz, process

logger = logging.getLogger("shops.services")

HIGH_CONFIDENCE_THRESHOLD = 85.0

SYNONYM_MAP: dict[str, str] = {
    "laptop": "electronics",
    "notebook": "electronics",
    "smartphone": "electronics",
    "phone": "electronics",
    "tablet": "electronics",
    "headphones": "electronics",
    "speaker": "electronics",
    "gaming": "gaming",
    "gamepad": "gaming",
    "console": "gaming",
    "kitchen": "home-kitchen",
    "cookware": "home-kitchen",
    "bedding": "home-kitchen",
    "furniture": "home-kitchen",
    "decor": "home-kitchen",
    "sport": "sports-outdoors",
    "fitness": "sports-outdoors",
    "outdoor": "sports-outdoors",
    "camping": "sports-outdoors",
    "bicycle": "sports-outdoors",
    "beauty": "beauty",
    "cosmetic": "beauty",
    "perfume": "beauty",
    "skincare": "beauty",
    "book": "books",
    "novel": "books",
    "toy": "toys-kids",
    "kids": "toys-kids",
    "children": "toys-kids",
    "lego": "toys-kids",
    "clothing": "fashion",
    "apparel": "fashion",
    "shoes": "fashion",
    "dress": "fashion",
    "jacket": "fashion",
    "food": "food-sweets",
    "wine": "food-sweets",
    "chocolate": "food-sweets",
    "coffee": "food-sweets",
    "experience": "experiences",
    "voucher": "experiences",
    "trip": "travel",
    "tour": "travel",
    "ticket": "experiences",
    "car": "auto",
    "automotive": "auto",
    "pet": "pets",
    "office": "office",
}


@dataclass
class MatchResult:
    category: object
    confidence: float
    needs_review: bool


def _normalise(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.lower()
    text = re.sub(r"[/\\\-,|>»›→]", " ", text)
    text = re.sub(r"[^\w\s]", "", text)
    return " ".join(text.split())


class CategoryMatcher:
    _cache: Optional[list[tuple]] = None

    def __init__(self, threshold: float = HIGH_CONFIDENCE_THRESHOLD):
        self.threshold = threshold

    @classmethod
    def _load_categories(cls) -> list[tuple]:
        if cls._cache is None:
            cls._cache = cls._fetch_from_db()
        return cls._cache

    @classmethod
    def _fetch_from_db(cls) -> list[tuple]:
        from gifts.models import Category

        rows = []
        for category in Category.objects.filter(is_active=True).only("id", "name", "slug"):
            rows.append((category.id, category.name, category.slug, category))
        logger.debug("CategoryMatcher loaded %d categories from DB", len(rows))
        return rows

    @classmethod
    def invalidate_cache(cls) -> None:
        cls._cache = None
        logger.debug("CategoryMatcher cache invalidated")

    def match(self, raw: str, shop=None) -> MatchResult:
        if not raw or not raw.strip():
            return MatchResult(category=None, confidence=0.0, needs_review=True)

        normalized = _normalise(raw)
        categories = self._load_categories()
        if not categories:
            return MatchResult(category=None, confidence=0.0, needs_review=True)

        alias_result = self._match_alias(shop, raw, normalized)
        if alias_result is not None:
            return alias_result

        synonym_result = self._match_synonym(normalized, categories)
        if synonym_result is not None:
            self._upsert_alias(shop, raw, normalized, synonym_result.category, synonym_result.confidence, False)
            return synonym_result

        fuzzy_result = self._match_fuzzy(raw, normalized, categories)
        self._upsert_alias(
            shop,
            raw,
            normalized,
            None if fuzzy_result.needs_review else fuzzy_result.category,
            fuzzy_result.confidence,
            fuzzy_result.needs_review,
        )
        return fuzzy_result

    def _match_alias(self, shop, raw: str, normalized: str) -> MatchResult | None:
        if shop is None:
            return None

        from .models import ShopCategoryAlias

        exact_alias = (
            ShopCategoryAlias.objects.select_related("category")
            .filter(shop=shop, raw_category__iexact=raw)
            .first()
        )
        if exact_alias is not None:
            if exact_alias.status == ShopCategoryAlias.STATUS_MATCHED and exact_alias.category:
                return MatchResult(
                    category=exact_alias.category,
                    confidence=exact_alias.confidence or 100.0,
                    needs_review=False,
                )
            return MatchResult(
                category=exact_alias.category,
                confidence=exact_alias.confidence or 0.0,
                needs_review=exact_alias.status != ShopCategoryAlias.STATUS_MATCHED,
            )

        normalized_alias = (
            ShopCategoryAlias.objects.select_related("category")
            .filter(
                shop=shop,
                normalized_category=normalized,
                status=ShopCategoryAlias.STATUS_MATCHED,
                category__isnull=False,
            )
            .order_by("-confidence", "id")
            .first()
        )
        if normalized_alias is None:
            return None

        self._upsert_alias(
            shop,
            raw,
            normalized,
            normalized_alias.category,
            normalized_alias.confidence or 100.0,
            False,
        )
        return MatchResult(
            category=normalized_alias.category,
            confidence=normalized_alias.confidence or 100.0,
            needs_review=False,
        )

    def _match_synonym(self, normalized: str, categories: list[tuple]) -> MatchResult | None:
        for keyword, slug in SYNONYM_MAP.items():
            if keyword in normalized:
                matched = self._find_by_slug(categories, slug)
                if matched:
                    return MatchResult(category=matched, confidence=100.0, needs_review=False)
        return None

    def _match_fuzzy(self, raw: str, normalized: str, categories: list[tuple]) -> MatchResult:
        choices = {row[1]: row[3] for row in categories}
        best = process.extractOne(
            normalized,
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
            "CategoryMatcher fuzzy '%s' -> '%s' score=%.1f needs_review=%s",
            raw, matched_name, score, needs_review,
        )
        return MatchResult(category=category_obj, confidence=score, needs_review=needs_review)

    def _upsert_alias(self, shop, raw: str, normalized: str, category, confidence: float, needs_review: bool) -> None:
        if shop is None or not raw.strip():
            return

        from .models import ShopCategoryAlias

        status = (
            ShopCategoryAlias.STATUS_PENDING
            if needs_review else
            ShopCategoryAlias.STATUS_MATCHED
        )
        defaults = {
            "normalized_category": normalized,
            "category": category,
            "confidence": confidence,
            "status": status,
        }
        ShopCategoryAlias.objects.update_or_create(
            shop=shop,
            raw_category=raw[:300],
            defaults=defaults,
        )

    @staticmethod
    def _find_by_slug(categories: list[tuple], slug: str):
        for _, _, category_slug, category_obj in categories:
            if category_slug == slug:
                return category_obj
        return None
