from django.test import TestCase, Client
from django.urls import reverse
from shop.models import Category, Product, Order, OrderItem

class ModelTests(TestCase):
    def setUp(self):
        """Set up test instances before each model test method."""
        self.category = Category.objects.create(
            name='Electronics',
            slug='electronics'
        )
        self.product = Product.objects.create(
            category=self.category,
            name='Wireless Headphones',
            slug='wireless-headphones',
            price=4999.00,
            stock=10,
            available=True,
            description='High quality wireless headphones.'
        )
        self.order = Order.objects.create(
            first_name='Rishabh',
            email='rishabh@example.com',
            paid=False
        )

    def test_category_creation(self):
        """Test Category model creation and string representation."""
        self.assertEqual(self.category.name, 'Electronics')
        self.assertEqual(str(self.category), 'Electronics')

    def test_product_creation(self):
        """Test Product model fields and relation to Category."""
        self.assertEqual(self.product.name, 'Wireless Headphones')
        self.assertEqual(self.product.category.name, 'Electronics')
        self.assertEqual(self.product.price, 4999.00)
        self.assertEqual(self.product.stock, 10)
        self.assertTrue(self.product.available)
        self.assertEqual(str(self.product), 'Wireless Headphones')

    def test_order_creation(self):
        """Test Order and OrderItem relationships."""
        order_item = OrderItem.objects.create(
            order=self.order,
            product=self.product,
            price=self.product.price,
            quantity=2
        )
        self.assertEqual(self.order.first_name, 'Rishabh')
        self.assertFalse(self.order.paid)
        self.assertEqual(order_item.product.name, 'Wireless Headphones')
        self.assertEqual(order_item.quantity, 2)


class ViewTests(TestCase):
    def setUp(self):
        """Set up client and test data for HTTP view requests."""
        self.client = Client()
        self.category = Category.objects.create(
            name='Audio',
            slug='audio'
        )
        self.product = Product.objects.create(
            category=self.category,
            name='Bluetooth Speaker',
            slug='bluetooth-speaker',
            price=1799.00,
            stock=15,
            available=True,
            description='Portable bluetooth speaker.'
        )

    def test_product_list_view(self):
        """Test product list page status code and template."""
        response = self.client.get(reverse('shop:product_list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Bluetooth Speaker')

    def test_product_list_by_category_view(self):
        """Test category filter view."""
        response = self.client.get(
            reverse('shop:product_list_by_category', args=['audio'])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Bluetooth Speaker')

    def test_product_detail_view(self):
        """Test individual product detail page."""
        response = self.client.get(
            reverse('shop:product_detail', args=[self.product.id, self.product.slug])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Bluetooth Speaker')
        self.assertContains(response, '1799')
