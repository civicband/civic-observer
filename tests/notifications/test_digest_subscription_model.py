from django.db.utils import IntegrityError
from django.test import TestCase

from notifications.models import DigestSubscription
from tests.factories import MuniFactory, UserFactory


class UserTimezoneTest(TestCase):
    def test_user_timezone_field_exists(self):
        user = UserFactory()
        self.assertEqual(user.timezone, "America/New_York")

    def test_user_timezone_default_is_america_new_york(self):
        user = UserFactory()
        self.assertEqual(user.timezone, "America/New_York")

    def test_user_timezone_can_be_set(self):
        user = UserFactory(timezone="America/Los_Angeles")
        self.assertEqual(user.timezone, "America/Los_Angeles")


class DigestSubscriptionModelTest(TestCase):
    def test_create_subscription(self):
        user = UserFactory()
        muni = MuniFactory()
        sub = DigestSubscription.objects.create(user=user, municipality=muni)
        self.assertTrue(sub.is_active)
        self.assertIsNone(sub.last_digest_sent)
        self.assertIn(user.email, str(sub))
        self.assertIn(muni.name, str(sub))

    def test_unique_together_constraint(self):
        user = UserFactory()
        muni = MuniFactory()
        DigestSubscription.objects.create(user=user, municipality=muni)
        with self.assertRaises(IntegrityError):
            DigestSubscription.objects.create(user=user, municipality=muni)

    def test_can_create_multiple_subscriptions_for_different_municipalities(self):
        user = UserFactory()
        muni1 = MuniFactory()
        muni2 = MuniFactory(subdomain="different-muni")
        DigestSubscription.objects.create(user=user, municipality=muni1)
        DigestSubscription.objects.create(user=user, municipality=muni2)
        self.assertEqual(user.digest_subscriptions.count(), 2)

    def test_can_create_subscription_for_same_muni_different_user(self):
        user1 = UserFactory()
        user2 = UserFactory(email="other@example.com")
        muni = MuniFactory()
        DigestSubscription.objects.create(user=user1, municipality=muni)
        DigestSubscription.objects.create(user=user2, municipality=muni)
        self.assertEqual(
            DigestSubscription.objects.filter(municipality=muni).count(), 2
        )

    def test_db_table_name(self):
        self.assertEqual(DigestSubscription._meta.db_table, "digest_subscription")

    def test_ordering_by_municipality_name(self):
        muni_a = MuniFactory(name="Alpha")
        muni_b = MuniFactory(name="Bravo")
        user = UserFactory()
        DigestSubscription.objects.create(user=user, municipality=muni_b)
        DigestSubscription.objects.create(user=user, municipality=muni_a)
        subs = list(DigestSubscription.objects.filter(user=user))
        self.assertEqual(subs[0].municipality.name, "Alpha")
        self.assertEqual(subs[1].municipality.name, "Bravo")
