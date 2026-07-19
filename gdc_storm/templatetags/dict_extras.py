import re

from django import template

register = template.Library()

_CPC_PREFIX_RE = re.compile(r'^CPC-[A-Z]+\[\d{2,3}\]-(.+)$', re.IGNORECASE)


@register.filter
def get_item(dictionary, key):
    return dictionary.get(key, '')


@register.filter
def mission_display_name(name):
    """Retire le préfixe CPC-TYPE[NN]- et remplace -/_ par des espaces."""
    if not name:
        return ''
    text = str(name)
    match = _CPC_PREFIX_RE.match(text)
    if match:
        text = match.group(1)
    return text.replace('-', ' ').replace('_', ' ')


@register.filter
def mission_short_name(name):
    """Alias historique : même rendu lisible que mission_display_name."""
    return mission_display_name(name)
