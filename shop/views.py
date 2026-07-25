import razorpay
from django.conf import settings
from django.shortcuts import render, get_object_or_404, redirect
from django.views import View
from django.views.generic import ListView, DetailView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth import login, logout
from django.contrib import messages
from django.utils.crypto import get_random_string
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator

from .models import Category, Product, Order, OrderItem, Wishlist, CartItem
from .cart import Cart
from .forms import RegisterForm

from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver

# Initialize Razorpay Client
razorpay_client = razorpay.Client(
    auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET)
)


# ==============================================================================
# HELPER FUNCTIONS
# ==============================================================================

def merge_guest_cart_to_user(request, user):
    """
    Merges session cart items into the user's database CartItem records upon login/registration.
    """
    cart_key = getattr(settings, 'CART_SESSION_ID', 'cart')
    session_cart = request.session.get(cart_key, {})

    # 1. Sync guest session items into Database
    for product_id, item_data in session_cart.items():
        try:
            product = Product.objects.get(id=int(product_id))
            cart_item, created = CartItem.objects.get_or_create(
                user=user, 
                product=product,
                defaults={'quantity': item_data['quantity']}
            )
            if not created:
                cart_item.quantity += item_data['quantity']
                cart_item.save()
        except (Product.DoesNotExist, ValueError):
            continue

    # 2. Re-populate session from DB for consistency
    user_db_items = CartItem.objects.filter(user=user)
    updated_session_cart = {}
    
    for db_item in user_db_items:
        product_id = str(db_item.product.id)
        updated_session_cart[product_id] = {
            'quantity': db_item.quantity,
            'price': str(db_item.product.get_discount_price())
        }

    request.session[cart_key] = updated_session_cart
    request.session.modified = True


# ==============================================================================
# AUTHENTICATION VIEWS
# ==============================================================================

class RegisterView(View):
    def get(self, request):
        form = RegisterForm()
        return render(request, 'registration/register.html', {'form': form})

    def post(self, request):
        form = RegisterForm(request.POST)
        if form.is_valid():
            user = form.save(commit=False)
            user.set_password(form.cleaned_data['password'])
            user.save()
            login(request, user)
            merge_guest_cart_to_user(request, user)  # Sync guest cart to DB
            messages.success(request, f"Welcome {user.username}, account created successfully!")
            return redirect('shop:product_list')
        return render(request, 'registration/register.html', {'form': form})


class CustomLogoutView(View):
    def get(self, request):
        return self._perform_logout(request)

    def post(self, request):
        return self._perform_logout(request)

    def _perform_logout(self, request):
        # 1. Save active cart data before flushing session
        cart_key = getattr(settings, 'CART_SESSION_ID', 'cart')
        saved_cart = request.session.get(cart_key, {})

        # 2. Perform standard logout
        logout(request)

        # 3. Restore cart back into the new anonymous guest session
        request.session[cart_key] = saved_cart
        request.session.modified = True

        messages.info(request, "You have been logged out.")
        return redirect('shop:product_list')


# ==============================================================================
# CATALOG & PRODUCT VIEWS
# ==============================================================================

class ProductListView(ListView):
    model = Product
    template_name = 'shop/product_list.html'
    context_object_name = 'products'

    def get_queryset(self):
        queryset = Product.objects.filter(is_active=True)
        category_slug = self.kwargs.get('category_slug')
        if category_slug:
            self.category = get_object_or_404(Category, slug=category_slug)
            queryset = queryset.filter(category=self.category)
        else:
            self.category = None

        search = self.request.GET.get('q')
        if search:
            queryset = queryset.filter(name__icontains=search)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['categories'] = Category.objects.all()
        context['selected_category'] = getattr(self, 'category', None)
        return context


class ProductDetailView(DetailView):
    model = Product
    template_name = 'shop/product_detail.html'
    context_object_name = 'product'


# ==============================================================================
# CART VIEWS (Protected with LoginRequiredMixin)
# ==============================================================================

class CartDetailView(View):
    def get(self, request):
        cart = Cart(request)
        return render(request, 'shop/cart.html', {'cart': cart})


class CartAddView(LoginRequiredMixin, View):  # Protected: requires login
    def post(self, request, product_id):
        return self._add_to_cart(request, product_id)

    def get(self, request, product_id):
        # Triggered when redirected here after logging in via GET
        return self._add_to_cart(request, product_id)

    def _add_to_cart(self, request, product_id):
        cart = Cart(request)
        product = get_object_or_404(Product, id=product_id)
        
        try:
            quantity = int(request.POST.get('quantity', 1)) if request.method == 'POST' else 1
        except (ValueError, TypeError):
            quantity = 1

        override = request.POST.get('override', False)
        
        cart.add(product=product, quantity=quantity, override_quantity=override)
        messages.success(request, f"Added '{product.name}' to your cart.")
        return redirect('shop:cart_detail')


class CartRemoveView(LoginRequiredMixin, View):  # Protected: requires login
    def post(self, request, product_id):
        cart = Cart(request)
        product = get_object_or_404(Product, id=product_id)
        cart.remove(product)
        messages.info(request, f"Removed '{product.name}' from cart.")
        return redirect('shop:cart_detail')


# ==============================================================================
# CHECKOUT & PAYMENT VIEWS
# ==============================================================================

class CheckoutView(LoginRequiredMixin, View):
    def get(self, request):
        cart = Cart(request)
        if len(cart) == 0:
            messages.warning(request, "Your cart is empty.")
            return redirect('shop:product_list')
        return render(request, 'shop/checkout.html', {'cart': cart})

    def post(self, request):
        cart = Cart(request)
        if len(cart) == 0:
            return redirect('shop:product_list')

        address = request.POST.get('shipping_address')
        payment_method = request.POST.get('payment_method', 'COD')

        order = Order.objects.create(
            user=request.user,
            order_id=f"ORD-{get_random_string(8).upper()}",
            total_amount=cart.get_total_price(),
            shipping_address=address,
            payment_method=payment_method,
            payment_status='pending',
            order_status='pending'
        )

        for item in cart:
            OrderItem.objects.create(
                order=order,
                product=item['product'],
                price=item['price'],
                quantity=item['quantity']
            )

        if payment_method == 'RAZORPAY':
            return redirect('shop:initiate_payment', order_id=order.order_id)
        else:
            cart.clear()
            messages.success(request, f"Order #{order.order_id} placed successfully!")
            return redirect('shop:order_detail', order_id=order.order_id)


class InitiatePaymentView(LoginRequiredMixin, View):
    def get(self, request, order_id):
        order = get_object_or_404(Order, order_id=order_id, user=request.user)
        amount_in_paise = int(order.total_amount * 100)

        razorpay_order = razorpay_client.order.create({
            "amount": amount_in_paise,
            "currency": "INR",
            "payment_capture": "1"
        })

        context = {
            'order': order,
            'razorpay_order_id': razorpay_order['id'],
            'razorpay_merchant_key': settings.RAZORPAY_KEY_ID,
            'amount': amount_in_paise,
            'currency': 'INR',
        }
        return render(request, 'shop/payment.html', context)


@method_decorator(csrf_exempt, name='dispatch')
class PaymentCallbackView(View):
    def post(self, request):
        payment_id = request.POST.get('razorpay_payment_id', '')
        razorpay_order_id = request.POST.get('razorpay_order_id', '')
        signature = request.POST.get('razorpay_signature', '')
        order_id = request.POST.get('order_id', '')

        order = get_object_or_404(Order, order_id=order_id)
        params_dict = {
            'razorpay_order_id': razorpay_order_id,
            'razorpay_payment_id': payment_id,
            'razorpay_signature': signature
        }

        try:
            razorpay_client.utility.verify_payment_signature(params_dict)
            order.payment_status = 'paid'
            order.order_status = 'processing'
            order.save()

            cart = Cart(request)
            cart.clear()

            messages.success(request, f"Payment successful for Order #{order.order_id}!")
            return redirect('shop:order_detail', order_id=order.order_id)
        except Exception:
            order.payment_status = 'failed'
            order.save()
            messages.error(request, "Payment verification failed.")
            return redirect('shop:checkout')


class OrderDetailView(LoginRequiredMixin, View):
    def get(self, request, order_id):
        order = get_object_or_404(Order, order_id=order_id, user=request.user)
        return render(request, 'shop/order_detail.html', {'order': order})


class OrderListView(LoginRequiredMixin, View):
    def get(self, request):
        orders = Order.objects.filter(user=request.user)
        return render(request, 'shop/order_list.html', {'orders': orders})


# ==============================================================================
# WISHLIST VIEWS
# ==============================================================================

class WishlistToggleView(LoginRequiredMixin, View):
    def post(self, request, product_id):
        product = get_object_or_404(Product, id=product_id)
        wishlist_item, created = Wishlist.objects.get_or_create(user=request.user, product=product)
        
        if not created:
            wishlist_item.delete()
            messages.info(request, f"Removed '{product.name}' from your wishlist.")
        else:
            messages.success(request, f"Added '{product.name}' to your wishlist.")
            
        return redirect('shop:product_detail', slug=product.slug)


class WishlistView(LoginRequiredMixin, View):
    def get(self, request):
        wishlist = Wishlist.objects.filter(user=request.user)
        return render(request, 'shop/wishlist.html', {'wishlist': wishlist})


# ==============================================================================
# SIGNALS (Automated Cart Sync on User Login)
# ==============================================================================

@receiver(user_logged_in)
def on_user_logged_in(sender, request, user, **kwargs):
    """
    Triggers automatically whenever a user logs in (via standard Django login forms, 
    social auth, or admin) to merge their guest session cart with their DB items.
    """
    merge_guest_cart_to_user(request, user)