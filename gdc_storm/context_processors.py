"""Context processors for templates."""

from gdc_storm.social_auth import (
    any_social_auth_enabled,
    enabled_provider_ids,
    get_social_auth_enabled,
    provider_label,
)


def social_auth_context(request):
    enabled = get_social_auth_enabled()
    show_connections = any_social_auth_enabled()
    if request.user.is_authenticated and not show_connections:
        # Afficher le menu si l'utilisateur a déjà des liens sociaux
        # (provider désactivé ensuite) — import paresseux pour éviter
        # les imports circulaires au démarrage.
        try:
            show_connections = request.user.socialaccount_set.exists()
        except Exception:
            show_connections = False

    return {
        "social_auth_enabled": enabled,
        "any_social_auth_enabled": any_social_auth_enabled(),
        "show_account_connections": show_connections,
        "enabled_social_providers": [
            {"id": pid, "label": provider_label(pid)}
            for pid in enabled_provider_ids()
        ],
        "user_has_usable_password": (
            request.user.has_usable_password()
            if request.user.is_authenticated
            else True
        ),
    }
