"""Adapters allauth : comptes sociaux en attente de validation admin."""

from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse

from allauth.account.adapter import DefaultAccountAdapter
from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter

from gdc_storm.social_auth import is_provider_enabled, provider_label


class AccountAdapter(DefaultAccountAdapter):
    def login(self, request, user):
        if not user.is_active:
            raise ImmediateHttpResponse(redirect("pending_approval"))
        return super().login(request, user)

    def pre_login(
        self,
        request,
        user,
        *,
        email_verification,
        signal_kwargs,
        email,
        signup,
        redirect_url,
    ):
        if not user.is_active:
            return self.respond_user_inactive(request, user)
        return super().pre_login(
            request,
            user,
            email_verification=email_verification,
            signal_kwargs=signal_kwargs,
            email=email,
            signup=signup,
            redirect_url=redirect_url,
        )

    def respond_user_inactive(self, request, user):
        return redirect("pending_approval")

    def is_open_for_signup(self, request):
        # Pas d'inscription locale classique — uniquement via social (si activé).
        return False


class SocialAccountAdapter(DefaultSocialAccountAdapter):
    def is_open_for_signup(self, request, sociallogin):
        provider = sociallogin.account.provider
        return is_provider_enabled(provider)

    def pre_social_login(self, request, sociallogin):
        provider = sociallogin.account.provider
        if not is_provider_enabled(provider):
            messages.error(
                request,
                f"La connexion {provider_label(provider)} n'est pas activée.",
            )
            raise ImmediateHttpResponse(redirect("login"))

        if sociallogin.is_existing and not sociallogin.user.is_active:
            raise ImmediateHttpResponse(redirect("pending_approval"))

    def populate_user(self, request, sociallogin, data):
        user = super().populate_user(request, sociallogin, data)
        provider = sociallogin.account.provider
        uid = sociallogin.account.uid
        base_username = f"{provider}_{uid}"[:150]
        user.username = base_username

        display = (
            data.get("username")
            or data.get("name")
            or data.get("personaname")
            or ""
        )
        if display:
            user.first_name = str(display)[:150]
        return user

    def save_user(self, request, sociallogin, form=None):
        user = super().save_user(request, sociallogin, form=form)
        # Nouveaux comptes sociaux : inactifs jusqu'à validation admin.
        user.is_active = False
        user.save(update_fields=["is_active"])
        return user

    def get_connect_redirect_url(self, request, socialaccount):
        return reverse("account_connections")

    def authentication_error(
        self,
        request,
        provider_id,
        error=None,
        exception=None,
        extra_context=None,
    ):
        if not is_provider_enabled(provider_id):
            messages.error(
                request,
                f"La connexion {provider_label(provider_id)} n'est pas activée.",
            )
            raise ImmediateHttpResponse(redirect("login"))
        return super().authentication_error(
            request,
            provider_id,
            error=error,
            exception=exception,
            extra_context=extra_context,
        )
