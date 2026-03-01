from django.urls import path
from . import views

app_name = 'gifts'

urlpatterns = [
    path("category/<slug:slug>/", views.category_detail, name="category_detail"),
]
