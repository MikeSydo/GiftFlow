from django.shortcuts import render
from gifts.models import Gift, Category


def home(request):
    """Main page"""

    categories = (
        Category.objects.filter(is_active=True, parent__isnull=True)
        .prefetch_related("subcategories")
        .order_by("order", "name")
    )

    catalog_data = []
    for cat in categories:
        top_gifts = (
            Gift.objects.filter(is_active=True, category__parent=cat)
            .select_related("category")
            .prefetch_related("tags")
            .order_by("-popularity_score", "-created_at")[:4]
        )
        # Always include the category — even if it has no gifts yet
        catalog_data.append(
            {
                "category": cat,
                "subcategories": cat.subcategories.filter(is_active=True).order_by(
                    "order", "name"
                ),
                "gifts": top_gifts,
            }
        )

    featured_gifts = (
        Gift.objects.filter(is_active=True, is_featured=True)
        .select_related("category")
        .prefetch_related("tags")
        .order_by("-popularity_score")[:6]
    )

    return render(
        request,
        "home/home.html",
        {
            "catalog_data": catalog_data,
            "featured_gifts": featured_gifts,
        },
    )
