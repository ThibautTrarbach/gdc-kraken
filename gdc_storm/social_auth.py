"""Helpers for optional Discord / Steam social authentication."""

from django.conf import settings


PROVIDER_LABELS = {
    "discord": "Discord",
    "steam": "Steam",
}


def get_social_auth_enabled():
    """Return {provider_id: bool} for each known social provider."""
    return getattr(
        settings,
        "SOCIAL_AUTH_ENABLED",
        {"discord": False, "steam": False},
    )


def is_provider_enabled(provider: str) -> bool:
    return bool(get_social_auth_enabled().get(provider))


def any_social_auth_enabled() -> bool:
    return any(get_social_auth_enabled().values())


def enabled_provider_ids():
    return [pid for pid, enabled in get_social_auth_enabled().items() if enabled]


def provider_label(provider: str) -> str:
    return PROVIDER_LABELS.get(provider, provider.title())
