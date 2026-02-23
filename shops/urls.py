from django.urls import path

from . import views

app_name = 'shops'

urlpatterns = [
    # Shops
    path('', views.ShopListView.as_view(), name='shop-list'),
    path('<slug:slug>/', views.ShopDetailView.as_view(), name='shop-detail'),

    # Product links
    path('products/', views.ProductLinkListView.as_view(), name='productlink-list'),
    path('products/<int:pk>/', views.ProductLinkDetailView.as_view(), name='productlink-detail'),
    path('products/<int:pk>/click/', views.ProductLinkClickView.as_view(), name='productlink-click'),
    path('products/<int:pk>/price-history/', views.PriceHistoryView.as_view(), name='price-history'),

    # Analytics (staff)
    path('analytics/', views.AnalyticsView.as_view(), name='analytics'),
    path('scraper-status/', views.ScraperStatusView.as_view(), name='scraper-status'),
    path('trigger-discovery/<slug:shop_slug>/', views.TriggerDiscoveryView.as_view(), name='trigger-discovery'),
]
