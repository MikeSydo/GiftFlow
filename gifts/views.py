from django.db.models import Prefetch
from django.shortcuts import get_object_or_404, render

from shops.models import ProductLink

from .models import Category, Gift


def _top_level_categories():
    return Category.objects.filter(is_active=True, parent__isnull=True).order_by(
        "order",
        "name",
    )


def category_detail(request, slug):
    """List all active gifts belonging to the given category."""
    category = get_object_or_404(Category, slug=slug, is_active=True)

    gifts = (
        Gift.objects.filter(is_active=True, category=category)
        .select_related("category")
        .prefetch_related("tags")
        .order_by("-popularity_score", "-created_at")
    )

    return render(
        request,
        "gifts/category.html",
        {
            "category": category,
            "gifts": gifts,
            "all_categories": _top_level_categories(),
        },
    )


def gift_detail(request, slug):
    offers_qs = (
        ProductLink.objects.select_related("shop")
        .order_by("-in_stock", "price", "-shop__priority", "id")
    )
    gift = get_object_or_404(
        Gift.objects.select_related("category").prefetch_related(
            "tags",
            Prefetch("productlinks", queryset=offers_qs),
        ),
        slug=slug,
        is_active=True,
    )

    offers = list(gift.productlinks.all())
    return render(
        request,
        "gifts/detail.html",
        {
            "gift": gift,
            "offers": offers,
            "all_categories": _top_level_categories(),
        },
    )
