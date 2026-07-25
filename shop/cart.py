from decimal import Decimal
from django.conf import settings
from .models import Product, CartItem

class Cart:
    def __init__(self, request):
        self.session = request.session
        self.request = request
        self.cart_session_id = getattr(settings, 'CART_SESSION_ID', 'cart')
        
        cart = self.session.get(self.cart_session_id)
        if not cart:
            cart = self.session[self.cart_session_id] = {}
        self.cart = cart

        # If user is logged in, merge database items with current session items
        if request.user.is_authenticated:
            self._sync_db_and_session()

    def _sync_db_and_session(self):
        """
        Ensures items in DB (CartItem) and items in session are combined 
        without deleting previous entries.
        """
        db_items = CartItem.objects.filter(user=self.request.user)
        
        # 1. Load any DB items into session if not already in session
        for item in db_items:
            product_id = str(item.product.id)
            if product_id not in self.cart:
                self.cart[product_id] = {
                    'quantity': item.quantity,
                    'price': str(item.product.get_discount_price())
                }
            else:
                # Keep whichever quantity is larger or synced
                if self.cart[product_id]['quantity'] < item.quantity:
                    self.cart[product_id]['quantity'] = item.quantity

        # 2. Sync session items back into DB so MySQL is up to date
        for product_id, item_data in self.cart.items():
            try:
                product = Product.objects.get(id=int(product_id))
                CartItem.objects.update_or_create(
                    user=self.request.user,
                    product=product,
                    defaults={'quantity': item_data['quantity']}
                )
            except (Product.DoesNotExist, ValueError):
                continue
                
        self.save()

    def add(self, product, quantity=1, override_quantity=False):
        product_id = str(product.id)
        price = product.get_discount_price()

        if product_id not in self.cart:
            self.cart[product_id] = {
                'quantity': 0,
                'price': str(price)
            }

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

        # Remove from DB if user is logged in
        if self.request.user.is_authenticated:
            CartItem.objects.filter(user=self.request.user, product=product).delete()

    def save(self):
        self.session.modified = True

    def __iter__(self):
        product_ids = self.cart.keys()
        products = Product.objects.filter(id__in=product_ids)
        
        # Create a deep copy of dict values so we don't pollute session serialization
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
            
        if self.cart_session_id in self.session:
            del self.session[self.cart_session_id]
            self.save()