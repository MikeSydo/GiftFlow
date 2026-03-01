from django.urls import path
from . import views

app_name = 'search'

urlpatterns = [
    path("", views.gift_search, name="gift_search"),
    path("api/", views.search_gifts_api, name="search_gifts_api"),
]
