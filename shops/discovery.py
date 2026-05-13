from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Iterable

from gifts.models import Category, Gift

from .models import ShopCategoryAlias, ShopSource


@dataclass(frozen=True)
class DiscoveryRequest:
    source_type: str
    value: str
    config: dict = field(default_factory=dict)
    discovery_mode: str = ""
    query_term: str | None = None


class QueryBuilder:
    DEFAULT_MAX_QUERIES = 25
    DEFAULT_GIFT_QUERY_LIMIT = 20

    def build(self, source: ShopSource) -> list[DiscoveryRequest]:
        mode = source.discovery_mode or ShopSource.default_discovery_mode_for(source.source_type)
        if mode == "feed":
            return [self._request_from_source(source)]
        if mode == "api_catalog":
            if source.source_type == "seed_query":
                return self._build_query_seed_requests(source)
            return [self._request_from_source(source)]
        if source.source_type == "category_url" and self._looks_like_url(source.value):
            return [self._request_from_source(source)]
        if mode in {"query_seed", "category_seed"}:
            return self._build_query_seed_requests(source)
        return [self._request_from_source(source)]

    def _build_query_seed_requests(self, source: ShopSource) -> list[DiscoveryRequest]:
        terms: list[str] = []
        mode = source.discovery_mode or ShopSource.default_discovery_mode_for(source.source_type)
        if source.value and not self._looks_like_url(source.value):
            terms.append(source.value)

        if mode == "query_seed":
            terms.extend(self._seed_keywords(source))
            if self._config_bool(source, "include_category_names", True):
                terms.extend(self._category_terms(source))
            if self._config_bool(source, "include_aliases", True):
                terms.extend(self._alias_terms(source))
            if self._config_bool(source, "include_gift_names", True):
                terms.extend(self._gift_terms(source))
        else:
            terms.extend(self._category_terms(source))
            terms.extend(self._alias_terms(source))
            terms.extend(self._seed_keywords(source))

        deduped = self._dedupe_terms(terms)
        max_queries = self._config_int(source, "max_queries", self.DEFAULT_MAX_QUERIES)
        requests = [
            DiscoveryRequest(
                source_type="seed_query",
                value=term,
                query_term=term,
                discovery_mode=mode,
                config=self._request_config(source, term),
            )
            for term in deduped[:max_queries]
        ]

        if requests:
            return requests

        if source.value:
            return [self._request_from_source(source)]
        return []

    def _seed_keywords(self, source: ShopSource) -> list[str]:
        keywords: list[str] = []
        keywords.extend(self._config_list(source.integration.request_config, "seed_keywords"))
        keywords.extend(self._config_list(source.config, "seed_keywords"))
        return keywords

    def _category_terms(self, source: ShopSource) -> list[str]:
        categories = source.integration.shop.categories.filter(is_active=True).only("name")
        if categories.exists():
            return [category.name for category in categories]
        return list(
            Category.objects.filter(is_active=True, parent__isnull=True)
            .order_by("order", "name")
            .values_list("name", flat=True)
        )

    def _alias_terms(self, source: ShopSource) -> list[str]:
        return list(
            ShopCategoryAlias.objects.filter(
                shop=source.integration.shop,
                status=ShopCategoryAlias.STATUS_MATCHED,
            )
            .exclude(raw_category="")
            .values_list("raw_category", flat=True)
        )

    def _gift_terms(self, source: ShopSource) -> list[str]:
        categories = list(source.integration.shop.categories.values_list("id", flat=True))
        queryset = Gift.objects.filter(is_active=True)
        if categories:
            queryset = queryset.filter(category_id__in=categories)
        limit = self._config_int(source, "gift_query_limit", self.DEFAULT_GIFT_QUERY_LIMIT)
        return list(
            queryset.order_by("-popularity_score", "-created_at")
            .values_list("name", flat=True)[:limit]
        )

    def _request_from_source(self, source: ShopSource) -> DiscoveryRequest:
        return DiscoveryRequest(
            source_type=source.source_type,
            value=source.value,
            config=dict(source.config or {}),
            discovery_mode=source.discovery_mode,
        )

    def _request_config(self, source: ShopSource, term: str) -> dict:
        config = dict(source.config or {})
        if config.get("category_hint"):
            return config

        category_hint = self._category_hint_from_term(source, term)
        if category_hint:
            config["category_hint"] = category_hint
        return config

    def _category_hint_from_term(self, source: ShopSource, term: str) -> str | None:
        matched_alias = (
            ShopCategoryAlias.objects.filter(
                shop=source.integration.shop,
                raw_category=term,
                status=ShopCategoryAlias.STATUS_MATCHED,
                category__isnull=False,
            )
            .select_related("category")
            .first()
        )
        if matched_alias and matched_alias.category:
            return matched_alias.category.name

        category = source.integration.shop.categories.filter(name=term).first()
        if category:
            return category.name

        category = Category.objects.filter(name=term, is_active=True).first()
        if category:
            return category.name
        return None

    @staticmethod
    def _looks_like_url(value: str) -> bool:
        lowered = value.lower().strip()
        return lowered.startswith("http://") or lowered.startswith("https://")

    @staticmethod
    def _dedupe_terms(terms: Iterable[str]) -> list[str]:
        seen: set[str] = set()
        deduped: list[str] = []
        for term in terms:
            normalized = " ".join(str(term).split()).strip()
            if not normalized:
                continue
            key = normalized.casefold()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(normalized)
        return deduped

    @staticmethod
    def _config_list(payload: dict, key: str) -> list[str]:
        raw_value = payload.get(key, [])
        if isinstance(raw_value, list):
            return [str(item).strip() for item in raw_value if str(item).strip()]
        if isinstance(raw_value, str) and raw_value.strip():
            return [item.strip() for item in raw_value.split(",") if item.strip()]
        return []

    @staticmethod
    def _config_bool(source: ShopSource, key: str, default: bool) -> bool:
        for payload in (source.config or {}, source.integration.request_config or {}):
            if key in payload:
                return bool(payload[key])
        return default

    @staticmethod
    def _config_int(source: ShopSource, key: str, default: int) -> int:
        for payload in (source.config or {}, source.integration.request_config or {}):
            if key in payload:
                try:
                    return int(payload[key])
                except (TypeError, ValueError):
                    return default
        return default


def request_as_namespace(request: DiscoveryRequest):
    return SimpleNamespace(
        source_type=request.source_type,
        value=request.value,
        config=request.config,
        discovery_mode=request.discovery_mode,
        query_term=request.query_term,
    )
