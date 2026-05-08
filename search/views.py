from django.shortcuts import render
from django.http import JsonResponse
from django.db.models import OuterRef, Subquery
from gifts.models import Gift, Category, Tag
from shops.models import ProductLink


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
    """
    AJAX-endpoint for gifts filtering.
    """

    if request.method != "GET":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    best_offer_subquery = ProductLink.objects.filter(
        gift_id=OuterRef("pk"),
        in_stock=True,
    ).order_by("price", "id")

    qs = (
        Gift.objects.filter(is_active=True)
        .select_related("category")
        .prefetch_related("tags")
        .annotate(best_offer_id=Subquery(best_offer_subquery.values("id")[:1]))
    )

    # Filters
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

    offer_ids = [gift.best_offer_id for gift in qs if gift.best_offer_id]
    best_offers = {
        offer.id: offer
        for offer in ProductLink.objects.select_related("shop").filter(id__in=offer_ids)
    }

    results = []
    for gift in qs:
        best_offer = best_offers.get(gift.best_offer_id)
        results.append(
            {
                "id": gift.id,
                "title": gift.name,
                "short_description": gift.short_description or "",
                "image": (
                    gift.image.url if gift.image else
                    gift.image_url or
                    (best_offer.image_url if best_offer else "")
                ),
                "category": gift.category.name if gift.category else "",
                "min_price": str(gift.min_price or 0),
                "popularity_score": gift.popularity_score,
                "tags": [t.name for t in gift.tags.all()],
                "best_offer": (
                    {
                        "id": best_offer.id,
                        "shop": best_offer.shop.name,
                        "seller_name": best_offer.seller_name or best_offer.shop.name,
                        "price": str(best_offer.price),
                        "original_price": str(best_offer.original_price) if best_offer.original_price else None,
                        "product_url": best_offer.product_url,
                        "image_url": best_offer.image_url,
                        "is_marketplace_offer": best_offer.is_marketplace_offer,
                    }
                    if best_offer else None
                ),
            }
        )

    return JsonResponse({"results": results, "count": len(results)})
