from django.urls import path
from . import views

app_name = 'gifts'

urlpatterns = [
    path("<slug:slug>/", views.gift_detail, name="gift_detail"),
    path("category/<slug:slug>/", views.legacy_category_redirect, name="category_detail"),
]
