from django.db.models import Prefetch
from django.shortcuts import get_object_or_404, redirect, render

from shops.models import ProductLink

from .models import Category, Gift


def _top_level_categories():
    return (
        Category.objects.filter(is_active=True, parent__isnull=True)
        .prefetch_related(
            Prefetch(
                "subcategories",
                queryset=Category.objects.filter(is_active=True).order_by("order", "name"),
            )
        )
        .order_by(
            "order",
            "name",
        )
    )


def parent_category_detail(request, parent_slug):
    """List active gifts from all subcategories under a top-level category."""
    category = get_object_or_404(
        Category,
        slug=parent_slug,
        parent__isnull=True,
        is_active=True,
    )

    gifts = (
        Gift.objects.filter(
            is_active=True,
            category__parent=category,
        )
        .select_related("category")
        .prefetch_related("tags")
        .order_by("-popularity_score", "-created_at")
    )
    subcategories = category.subcategories.filter(is_active=True).order_by(
        "order",
        "name",
    )

    return render(
        request,
        "gifts/category.html",
        {
            "category": category,
            "gifts": gifts,
            "subcategories": subcategories,
            "all_categories": _top_level_categories(),
            "current_top_category": category,
        },
    )


def subcategory_detail(request, parent_slug, subcategory_slug):
    """List all active gifts belonging to a specific subcategory."""
    parent = get_object_or_404(
        Category,
        slug=parent_slug,
        parent__isnull=True,
        is_active=True,
    )
    category = get_object_or_404(
        Category,
        slug=subcategory_slug,
        parent=parent,
        is_active=True,
    )

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
            "parent_category": parent,
            "gifts": gifts,
            "subcategories": parent.subcategories.filter(is_active=True).order_by(
                "order",
                "name",
            ),
            "all_categories": _top_level_categories(),
            "current_top_category": parent,
        },
    )


def legacy_category_redirect(request, slug):
    category = get_object_or_404(Category.objects.select_related("parent"), slug=slug, is_active=True)
    return redirect(category.get_absolute_url(), permanent=True)


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
            "current_top_category": gift.category.parent if gift.category and gift.category.parent_id else gift.category,
        },
    )
