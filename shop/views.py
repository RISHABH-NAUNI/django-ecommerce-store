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

    for product_id, item_data in session_cart.items():
        try:
            product = Product.objects.get(id=int(product_id))
            CartItem.objects.update_or_create(
                user=user, 
                product=product,
                defaults={'quantity': item_data['quantity']}
            )
        except (Product.DoesNotExist, ValueError):
            continue

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
            merge_guest_cart_to_user(request, user)
            messages.success(request, f"Welcome {user.username}, account created successfully!")
            return redirect('shop:product_list')
        return render(request, 'registration/register.html', {'form': form})


class CustomLogoutView(View):
    def get(self, request):
        return self._perform_logout(request)

    def post(self, request):
        return self._perform_logout(request)

    def _perform_logout(self, request):
        cart_key = getattr(settings, 'CART_SESSION_ID', 'cart')
        saved_cart = request.session.get(cart_key, {})

        logout(request)

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
# CART VIEWS
# ==============================================================================

class CartDetailView(View):
    def get(self, request):
        cart = Cart(request)
        return render(request, 'shop/cart.html', {'cart': cart})


class CartAddView(LoginRequiredMixin, View):
    def post(self, request, product_id):
        return self._add_to_cart(request, product_id)

    def get(self, request, product_id):
        return self._add_to_cart(request, product_id)

    def _add_to_cart(self, request, product_id):
        cart = Cart(request)
        product = get_object_or_404(Product, id=product_id)
        
        data = request.POST if request.method == 'POST' else request.GET

        try:
            requested_qty = int(data.get('quantity', 1))
            if requested_qty <= 0:
                requested_qty = 1
        except (ValueError, TypeError):
            requested_qty = 1

        raw_override = data.get('override', False)
        override = str(raw_override).lower() in ['true', '1', 't']

        # Determine target total quantity
        current_cart_qty = cart.cart.get(str(product.id), {}).get('quantity', 0)
        target_qty = requested_qty if override else (current_cart_qty + requested_qty)

        # Validate against available stock
        if target_qty > product.stock:
            messages.error(
                request, 
                f"Cannot add {requested_qty} item(s) of '{product.name}'. Only {product.stock} left in stock."
            )
            return redirect('shop:cart_detail')

        cart.add(product=product, quantity=requested_qty, override_quantity=override)
        messages.success(request, f"Updated '{product.name}' in your cart.")
        return redirect('shop:cart_detail')


class CartRemoveView(LoginRequiredMixin, View):
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

        # Stock Validation
        for item in cart:
            product = item['product']
            quantity = item['quantity']
            if product.stock < quantity:
                messages.error(
                    request, 
                    f"Insufficient stock for '{product.name}'. Only {product.stock} available."
                )
                return redirect('shop:cart_detail')

        address = request.POST.get('shipping_address')
        payment_method = request.POST.get('payment_method', 'COD')

        # Create Order
        order = Order.objects.create(
            user=request.user,
            order_id=f"ORD-{get_random_string(8).upper()}",
            total_amount=cart.get_total_price(),
            shipping_address=address,
            payment_method=payment_method,
            payment_status='pending',
            order_status='pending'
        )

        # Create Items & Deduct Stock
        for item in cart:
            product = item['product']
            quantity = item['quantity']

            OrderItem.objects.create(
                order=order,
                product=product,
                price=item['price'],
                quantity=quantity
            )

            product.stock -= quantity
            product.save()

        # Payment Routing
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

        try:
            razorpay_order = razorpay_client.order.create({
                "amount": amount_in_paise,
                "currency": "INR",
                "payment_capture": "1"
            })
        except Exception as e:
            messages.error(request, f"Razorpay initiation failed: {str(e)}")
            return redirect('shop:checkout')

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
    def post(self, request, *args, **kwargs):
        return self._process_payment(request)

    def get(self, request, *args, **kwargs):
        return self._process_payment(request)

    def _process_payment(self, request):
        data = request.POST if request.POST else request.GET

        payment_id = data.get('razorpay_payment_id', '')
        razorpay_order_id = data.get('razorpay_order_id', '')
        signature = data.get('razorpay_signature', '')
        order_id = data.get('order_id', '')

        if not order_id:
            messages.error(request, "Order ID missing.")
            return redirect('shop:checkout')

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


class CancelOrderView(LoginRequiredMixin, View):
    def post(self, request, order_id):
        order = get_object_or_404(Order, order_id=order_id, user=request.user)

        if order.order_status in ['pending', 'processing']:
            order.order_status = 'cancelled'
            order.save()

            for item in order.items.all():
                item.product.stock += item.quantity
                item.product.save()

            messages.success(request, f"Order #{order.order_id} has been cancelled.")
        else:
            messages.error(request, "This order cannot be cancelled.")

        return redirect('shop:order_detail', order_id=order.order_id)


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
# SIGNALS
# ==============================================================================

@receiver(user_logged_in)
def on_user_logged_in(sender, request, user, **kwargs):
    merge_guest_cart_to_user(request, user)