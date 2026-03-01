from django.shortcuts import render, get_object_or_404
from .models import Gift, Category


def category_detail(request, slug):
    """List all active gifts belonging to the given category."""
    category = get_object_or_404(Category, slug=slug, is_active=True)

    gifts = (
        Gift.objects.filter(is_active=True, category=category)
        .select_related("category")
        .prefetch_related("tags")
        .order_by("-popularity_score", "-created_at")
    )

    # Top-level categories for the header dropdown
    all_categories = (
        Category.objects.filter(is_active=True, parent__isnull=True)
        .order_by("order", "name")
    )

    return render(
        request,
        "gifts/category.html",
        {
            "category": category,
            "gifts": gifts,
            "all_categories": all_categories,
        },
    )
