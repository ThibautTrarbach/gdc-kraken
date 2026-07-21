"""Règles de liaison utilisateur ↔ joueur in-game (anti-usurpation)."""

from __future__ import annotations


def user_identity_names(user) -> set[str]:
    """Noms reconnus pour l'auto-liaison (username + profils sociaux)."""
    names = {user.username.casefold()}
    first = (user.first_name or '').strip()
    if first:
        names.add(first.casefold())
    try:
        from allauth.socialaccount.models import SocialAccount
    except ImportError:
        return names
    for sa in SocialAccount.objects.filter(user=user):
        extra = sa.extra_data or {}
        for key in ('username', 'global_name', 'personaname', 'name'):
            val = extra.get(key)
            if val:
                names.add(str(val).casefold())
    return names


def user_can_self_link_player(user, player, *, identities: set[str] | None = None) -> bool:
    """
    Liaison autorisée si le joueur est déjà lié à l'utilisateur,
    ou s'il est libre et que son nom correspond à une identité connue.
    """
    linked_ids = {u.id for u in player.users.all()}
    if linked_ids:
        return user.id in linked_ids
    player_name = (player.name or '').casefold()
    if not player_name:
        return False
    if identities is None:
        identities = user_identity_names(user)
    return player_name in identities


def linkable_player_ids(user, players) -> set[int]:
    """IDs des joueurs que l'utilisateur peut cocher (une seule lecture des identités)."""
    identities = user_identity_names(user)
    return {
        p.id for p in players
        if user_can_self_link_player(user, p, identities=identities)
    }
