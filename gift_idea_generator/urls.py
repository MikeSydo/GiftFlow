"""
URL configuration for gift_idea_generator project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from gifts import views as gift_views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('home.urls')),
    path(
        'categories/<slug:parent_slug>/<slug:subcategory_slug>/',
        gift_views.subcategory_detail,
        name='category_subcategory_detail',
    ),
    path(
        'categories/<slug:parent_slug>/',
        gift_views.parent_category_detail,
        name='category_parent_detail',
    ),
    path('gifts/', include('gifts.urls')),
    path('api/shops/', include('shops.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
