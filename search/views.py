from django.http import JsonResponse
from django.shortcuts import render

from gifts.models import Category, Tag

from .services import apply_gift_filters, base_gift_queryset, serialize_gift_results


def gift_search(request):
    """
    Gift search page:
    Step 1: category
    Step 2: occasion tags
    Step 3: people params
    Step 4: results
    """

    categories = (
        Category.objects.filter(is_active=True, parent__isnull=False).order_by(
            "parent__order",
            "parent__name",
            "order", "name"
        )
        .select_related("parent")
    )

    occasion_tags = Tag.objects.filter(tag_type="O").order_by("name")
    interest_tags = Tag.objects.filter(tag_type="I").order_by("name")
    relationship_tags = Tag.objects.filter(tag_type="R").order_by("name")
    age_group_tags = Tag.objects.filter(tag_type="A").order_by("name")

    return render(
        request,
        "search/search.html",
        {
            "categories": categories,
            "occasion_tags": occasion_tags,
            "interest_tags": interest_tags,
            "relationship_tags": relationship_tags,
            "age_group_tags": age_group_tags,
        },
    )


def search_gifts_api(request):
    """Synchronous DB-backed gift search."""

    if request.method != "GET":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    qs = apply_gift_filters(base_gift_queryset(), request.GET)
    results, count = serialize_gift_results(qs)
    return JsonResponse(
        {
            "results": results,
            "count": count,
        },
    )
