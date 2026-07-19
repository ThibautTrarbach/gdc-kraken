from django import template
from django.utils.safestring import mark_safe

from gdc_storm.pbo_extract import sanitize_briefing_html

register = template.Library()


@register.filter(name='safe_briefing')
def safe_briefing(value):
    """Sanitize puis marque sûr — defense in depth pour briefings déjà en base."""
    return mark_safe(sanitize_briefing_html(value or ''))
