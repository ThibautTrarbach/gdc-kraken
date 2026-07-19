import re

from django import template

register = template.Library()

_CPC_PREFIX_RE = re.compile(r'^CPC-[A-Z]+\[\d{2,3}\]-(.+)$', re.IGNORECASE)


@register.filter
def get_item(dictionary, key):
    return dictionary.get(key, '')


@register.filter
def mission_short_name(name):
    """Retire le préfixe CPC-TYPE[NN]- pour l'affichage."""
    if not name:
        return ''
    match = _CPC_PREFIX_RE.match(str(name))
    return match.group(1) if match else name
