from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from gdc_storm.social_auth import any_social_auth_enabled, is_provider_enabled
from gdc_storm.views import _can_disconnect_provider


class SocialAuthSettingsTests(TestCase):
    @override_settings(
        SOCIAL_AUTH_ENABLED={"discord": False, "steam": False},
        ANY_SOCIAL_AUTH_ENABLED=False,
    )
    def test_providers_disabled_by_default_shape(self):
        self.assertFalse(is_provider_enabled("discord"))
        self.assertFalse(is_provider_enabled("steam"))
        self.assertFalse(any_social_auth_enabled())

    @override_settings(
        SOCIAL_AUTH_ENABLED={"discord": True, "steam": False},
        ANY_SOCIAL_AUTH_ENABLED=True,
    )
    def test_discord_enabled_flag(self):
        self.assertTrue(is_provider_enabled("discord"))
        self.assertFalse(is_provider_enabled("steam"))
        self.assertTrue(any_social_auth_enabled())


class PendingApprovalViewTests(TestCase):
    def test_pending_page_renders(self):
        response = self.client.get(reverse("pending_approval"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "en attente de validation")

    def test_allauth_inactive_uses_styled_template(self):
        response = self.client.get(reverse("account_inactive"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "en attente de validation")
        self.assertNotContains(response, "Account Inactive")
        self.assertNotContains(response, "Sign Up")


class LoginSocialButtonsTests(TestCase):
    @override_settings(
        SOCIAL_AUTH_ENABLED={"discord": False, "steam": False},
        ANY_SOCIAL_AUTH_ENABLED=False,
    )
    def test_login_hides_social_buttons_when_disabled(self):
        response = self.client.get(reverse("login"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Continuer avec Discord")
        self.assertNotContains(response, "Continuer avec Steam")


class ChangePasswordTests(TestCase):
    def test_set_password_without_old_when_unusable(self):
        user = User.objects.create_user(username="social_user", password="unused")
        user.set_unusable_password()
        user.save()
        self.client.force_login(user)

        response = self.client.post(
            reverse("change_password"),
            {
                "new_password1": "secret12",
                "new_password2": "secret12",
            },
        )
        self.assertRedirects(response, reverse("home"))
        user.refresh_from_db()
        self.assertTrue(user.has_usable_password())
        self.assertTrue(user.check_password("secret12"))

    def test_change_password_requires_old_when_usable(self):
        user = User.objects.create_user(username="local_user", password="oldpass1")
        self.client.force_login(user)

        response = self.client.post(
            reverse("change_password"),
            {
                "old_password": "wrong",
                "new_password1": "secret12",
                "new_password2": "secret12",
            },
        )
        self.assertEqual(response.status_code, 200)
        user.refresh_from_db()
        self.assertTrue(user.check_password("oldpass1"))


class DisconnectGuardTests(TestCase):
    def test_cannot_disconnect_last_method(self):
        user = User.objects.create_user(username="u1", password="unused")
        user.set_unusable_password()
        user.save()
        linked = {"discord": object()}
        self.assertFalse(_can_disconnect_provider(user, "discord", linked))

    def test_can_disconnect_when_password_exists(self):
        user = User.objects.create_user(username="u2", password="pass1234")
        linked = {"discord": object()}
        self.assertTrue(_can_disconnect_provider(user, "discord", linked))

    def test_can_disconnect_when_other_provider(self):
        user = User.objects.create_user(username="u3", password="unused")
        user.set_unusable_password()
        user.save()
        linked = {"discord": object(), "steam": object()}
        self.assertTrue(_can_disconnect_provider(user, "discord", linked))


class ApproveAccountsAdminTests(TestCase):
    def test_approve_action_activates_users(self):
        from django.contrib.admin.sites import AdminSite
        from django.contrib.auth.models import User
        from gdc_storm.admin import CustomUserAdmin

        pending = User.objects.create_user(username="pending1", password="x")
        pending.is_active = False
        pending.save()

        admin_view = CustomUserAdmin(User, AdminSite())
        request = self.client.request().wsgi_request
        request.user = User.objects.create_superuser("admin", "a@b.c", "adminpass")

        admin_view.approve_accounts(request, User.objects.filter(pk=pending.pk))
        pending.refresh_from_db()
        self.assertTrue(pending.is_active)
