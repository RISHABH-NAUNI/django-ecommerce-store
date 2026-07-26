from django.urls import path
from . import views

app_name = 'shop'

urlpatterns = [
    path('', views.ProductListView.as_view(), name='product_list'),
    path('category/<slug:category_slug>/', views.ProductListView.as_view(), name='product_list_by_category'),
    path('product/<slug:slug>/', views.ProductDetailView.as_view(), name='product_detail'),
    path('cart/', views.CartDetailView.as_view(), name='cart_detail'),
    path('cart/add/<int:product_id>/', views.CartAddView.as_view(), name='cart_add'),
    path('cart/remove/<int:product_id>/', views.CartRemoveView.as_view(), name='cart_remove'),
    path('checkout/', views.CheckoutView.as_view(), name='checkout'),
    
    # CALLBACK MUST BE BEFORE <str:order_id> TO PREVENT 405 ERRORS
    path('payment/callback/', views.PaymentCallbackView.as_view(), name='payment_callback'),
    path('payment/<str:order_id>/', views.InitiatePaymentView.as_view(), name='initiate_payment'),
    
    path('orders/', views.OrderListView.as_view(), name='order_list'),
    path('order/<str:order_id>/', views.OrderDetailView.as_view(), name='order_detail'),
    path('wishlist/', views.WishlistView.as_view(), name='wishlist'),
    path('wishlist/toggle/<int:product_id>/', views.WishlistToggleView.as_view(), name='wishlist_toggle'),
    path('register/', views.RegisterView.as_view(), name='register'),
    path('logout/', views.CustomLogoutView.as_view(), name='logout'),
    path('order/cancel/<str:order_id>/', views.CancelOrderView.as_view(), name='cancel_order'),
]