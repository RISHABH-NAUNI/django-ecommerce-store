from decimal import Decimal
from django.conf import settings
from .models import Product, CartItem

class Cart:
    def __init__(self, request):
        self.session = request.session
        self.request = request
        self.cart_session_id = getattr(settings, 'CART_SESSION_ID', 'cart')
        
        cart = self.session.get(self.cart_session_id)
        if cart is None:
            cart = self.session[self.cart_session_id] = {}
        self.cart = cart

    def add(self, product, quantity=1, override_quantity=False):
        product_id = str(product.id)
        price = product.get_discount_price()

        if product_id not in self.cart:
            self.cart[product_id] = {
                'quantity': 0,
                'price': str(price)
            }

        # Convert string 'True'/'False' from form inputs to boolean safely
        if isinstance(override_quantity, str):
            override_quantity = override_quantity.lower() in ['true', '1', 't']

        if override_quantity:
            self.cart[product_id]['quantity'] = quantity
        else:
            self.cart[product_id]['quantity'] += quantity

        # Sync to Database if user is logged in
        if self.request.user.is_authenticated:
            CartItem.objects.update_or_create(
                user=self.request.user,
                product=product,
                defaults={'quantity': self.cart[product_id]['quantity']}
            )

        self.save()

    def remove(self, product):
        product_id = str(product.id)
        if product_id in self.cart:
            del self.cart[product_id]
            self.save()

        if self.request.user.is_authenticated:
            CartItem.objects.filter(user=self.request.user, product=product).delete()

    def save(self):
        self.session.modified = True

    def __iter__(self):
        product_ids = self.cart.keys()
        products = Product.objects.filter(id__in=product_ids)
        
        cart = {k: v.copy() for k, v in self.cart.items()}
        for product in products:
            product_id = str(product.id)
            if product_id in cart:
                cart[product_id]['product'] = product

        for item in cart.values():
            if 'product' not in item:
                continue
            item['price'] = Decimal(item['price'])
            item['total_price'] = item['price'] * item['quantity']
            yield item

    def __len__(self):
        return sum(item['quantity'] for item in self.cart.values())

    def get_total_price(self):
        return sum(Decimal(item['price']) * item['quantity'] for item in self.cart.values())

    def clear(self):
        if self.request.user.is_authenticated:
            CartItem.objects.filter(user=self.request.user).delete()
            
        self.cart.clear()
        if self.cart_session_id in self.session:
            del self.session[self.cart_session_id]
        self.save()