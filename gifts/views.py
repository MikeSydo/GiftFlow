from django.shortcuts import render
from django.http import JsonResponse
from .models import Gift, Category, Tag

def home(request):
    """Main page"""

    categories = (
        Category.objects.filter(is_active=True, parent__isnull=True)
        .prefetch_related("gift_set__tags")
        .order_by("order", "name")
    )

    catalog_data = []
    for cat in categories:
        top_gifts = (
            cat.gift_set.filter(is_active=True)
            .order_by("-popularity_score", "-created_at")[:4]
        )
        # Always include the category — even if it has no gifts yet
        catalog_data.append(
            {
                "category": cat,
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
        "gifts/home.html",
        {
            "catalog_data": catalog_data,
            "featured_gifts": featured_gifts,
        },
    )


def gift_search(request):
    """
        Gift search page:
        Step 1: category
        Step 2: occasion tags
        Step 3: people params (gender, age, budget)
        Step 4: results
    """

    categories = (
        Category.objects.filter(is_active=True, parent__isnull=True).order_by(
            "order", "name"
        )
    )

    occasion_tags = Tag.objects.filter(tag_type="O").order_by("name")
    interest_tags = Tag.objects.filter(tag_type="I").order_by("name")
    relationship_tags = Tag.objects.filter(tag_type="R").order_by("name")
    age_group_tags = Tag.objects.filter(tag_type="A").order_by("name")

    return render(
        request,
        "gifts/search.html",
        {
            "categories": categories,
            "occasion_tags": occasion_tags,
            "interest_tags": interest_tags,
            "relationship_tags": relationship_tags,
            "age_group_tags": age_group_tags,
        },
    )


def search_gifts_api(request):
    """
    AJAX-endpoint for gifts filtering.
    """

    if request.method != "GET":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    qs = Gift.objects.filter(is_active=True).select_related("category").prefetch_related(
        "tags"
    )

    #Filters
    category_id = request.GET.get("category")
    if category_id and category_id.isdigit():
        qs = qs.filter(category_id=int(category_id))

    gender = request.GET.get("gender")
    if gender in ("M", "F"):
        qs = qs.filter(gender__in=(gender, "U"))

    age = request.GET.get("age")
    if age and age.isdigit():
        age = int(age)
        qs = qs.filter(age_min__lte=age, age_max__gte=age)

    budget_min = request.GET.get("budget_min")
    budget_max = request.GET.get("budget_max")
    if budget_min:
        try:
            budget_min_val = float(budget_min)
            # Gift's min price should be >= user's min budget
            qs = qs.filter(min_price__gte=budget_min_val)
        except (ValueError, TypeError):
            pass  # Ignore invalid input
    if budget_max:
        try:
            budget_max_val = float(budget_max)
            # Gift's min price should be <= user's max budget
            qs = qs.filter(min_price__lte=budget_max_val)
        except (ValueError, TypeError):
            pass  # Ignore invalid input

    tag_ids = request.GET.get("tags")
    if tag_ids:
        ids = [int(x) for x in tag_ids.split(",") if x.isdigit()]
        if ids:
            qs = qs.filter(tags__id__in=ids).distinct()

    qs = qs.order_by("-popularity_score", "-created_at")[:20]

    results = []
    for gift in qs:
        results.append(
            {
                "id": gift.id,
                "title": gift.name,
                "short_description": gift.short_description or "",
                "image": gift.image.url if gift.image else "",
                "category": gift.category.name if gift.category else "",
                "min_price": str(gift.min_price or 0),
                "popularity_score": gift.popularity_score,
                "tags": [t.name for t in gift.tags.all()],
            }
        )

    return JsonResponse({"results": results, "count": len(results)})