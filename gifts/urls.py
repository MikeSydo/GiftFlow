from django.urls import path
from . import views

app_name = 'gifts'

urlpatterns = [
    path("", views.home, name="home"),
    path("search/", views.gift_search, name="gift_search"),
    path("api/search/", views.search_gifts_api, name="search_gifts_api"),
    path("category/<slug:slug>/", views.category_detail, name="category_detail"),
]