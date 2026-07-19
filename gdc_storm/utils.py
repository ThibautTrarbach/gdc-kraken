# Fonctions utilitaires
import re

from django.core.cache import cache

from .models import Mission

SESSION_LIST_CACHE_KEY = 'session_list_data_v1'


def invalidate_session_list_cache():
    """Invalide le cache de la liste des sessions après toute mutation."""
    cache.delete(SESSION_LIST_CACHE_KEY)


def strip_mission_version(mission_name):
    """Retourne (nom sans suffixe -Vn/-vn, numéro de version ou '')."""
    if not mission_name:
        return '', ''
    version_match = re.search(r'-[Vv](\d+)$', mission_name)
    if not version_match:
        return mission_name, ''
    version = version_match.group(1)
    name_no_version = re.sub(r'-[Vv]\d+$', '', mission_name)
    return name_no_version, version


def normalize_map_code(map_name):
    """Normalise le code carte (worldName) en minuscules."""
    return (map_name or '').strip().lower()


def find_missions_for_session(mission_name, map_name):
    """
    Missions dont map et nom correspondent à la session (casse ignorée).
    Retourne (matches, name_no_version, version, map_normalized).
    """
    name_no_version, version = strip_mission_version(mission_name or '')
    map_normalized = normalize_map_code(map_name)
    if not name_no_version or not map_normalized:
        return [], name_no_version, version, map_normalized
    name_key = name_no_version.casefold()
    # Filtre SQL sur map + nom (iexact) ; casefold final pour alignement unicode
    candidates = Mission.objects.filter(
        map__iexact=map_normalized,
        name__iexact=name_no_version,
    )
    matches = [m for m in candidates if (m.name or '').casefold() == name_key]
    return matches, name_no_version, version, map_normalized


def find_mission_for_session(mission_name, map_name):
    """
    Première mission correspondante, ou None.
    Utilisé à la création de session (comportement historique : premier match).
    """
    matches, name_no_version, version, map_normalized = find_missions_for_session(
        mission_name, map_name
    )
    mission = matches[0] if matches else None
    return mission, name_no_version, version, map_normalized


def parse_mission_filename(filename):
    """
    Parse strict d'un nom de fichier PBO.
    Format : CPC-TYPE[XX]-Nom_De_La_Mission-VY.nom_de_map.pbo
    """
    allowed_types = '|'.join([choice[0] for choice in Mission.TYPE_CHOICES])
    name_chars = r"[\w\d\s\-\_\(\)@#%&'éèàùâêîôûäëïöüçÉÈÀÙÂÊÎÔÛÄËÏÖÜÇ]+"
    pattern = (
        rf"^(CPC-({allowed_types})\[(\d{{2,3}})\]-{name_chars})"
        rf"-([Vv]\d+)\.(.+)\.pbo$"
    )
    match = re.match(pattern, filename)
    if not match:
        return None
    return match.groups()


def parse_mission_filename_lenient(filename):
    """
    Parse assoupli (récup) : accepte _V / -_V et des tags après la version (-HC, -(HC)).
    Conserve la structure CPC-TYPE[XX]-Nom…version.map.pbo.
    """
    allowed_types = '|'.join([choice[0] for choice in Mission.TYPE_CHOICES])
    name_char = r"[\w\d\s\-\_\(\)@#%&'éèàùâêîôûäëïöüçÉÈÀÙÂÊÎÔÛÄËÏÖÜÇ]"
    optional_tags = r"(?:-(?:\([A-Za-z0-9]+\)|[A-Za-z0-9]+))*"
    pattern = (
        rf"^(CPC-({allowed_types})\[(\d{{2,3}})\]-{name_char}+?)"
        rf"[-_]+([Vv]\d+){optional_tags}\.(.+)\.pbo$"
    )
    match = re.match(pattern, filename)
    if not match:
        return None
    return match.groups()


# LEGACY ONLY - Fonctions utilitaires pour l'import massif de missions .pbo
def legacy_parse_mission_filename(filename):
    # Expected output:
    # mission_name, mission_type, max_players, version, map_name
    allowed_types = '|'.join([choice[0] for choice in Mission.TYPE_CHOICES])
    name_chars = r"[\w\d\s\-\_\(\)@#%&'éèàùâêîôûäëïöüçÉÈÀÙÂÊÎÔÛÄËÏÖÜÇ]+"
    pattern = rf"^(CPC-({allowed_types})\[(\d{{2,3}})\]-?{name_chars})\.(.+)\.pbo$"
    match = re.match(pattern, filename)
    if not match:
        return None
    mission_name, mission_type, max_players, map_name = match.groups()

    # Essaie d'abord le format strict, puis le format récup (tags / _V)
    version_match = re.match(
        rf"^(CPC-({allowed_types})\[(\d{{2,3}})\]-{name_chars})-([Vv]\d+)",
        filename,
    )
    if not version_match:
        version_match = parse_mission_filename_lenient(filename)
        if version_match:
            return version_match
        version = "V1"
    else:
        groups = list(version_match.groups())
        mission_name = groups[0]
        version = groups[3]
    return mission_name, mission_type, max_players, version, map_name


def recup_parse_mission_filename(filename):
    """
    Parse récup : strict, puis assoupli (tags HC, _V), puis legacy, sinon stem/map.
    Retourne ((name, type, max_players, version, map), relaxed) ou None.
    """
    import os

    if not filename or not str(filename).lower().endswith('.pbo'):
        return None

    strict = parse_mission_filename(filename)
    if strict:
        return strict, False

    lenient = parse_mission_filename_lenient(filename)
    if lenient:
        return lenient, True

    legacy = legacy_parse_mission_filename(filename)
    if legacy:
        return legacy, True

    base = os.path.basename(filename)[:-4]
    if '.' in base:
        stem, map_name = base.rsplit('.', 1)
    else:
        stem, map_name = base, 'unknown'
    mission_name = (stem or 'Mission-Recup').strip()
    return (mission_name, 'CO', '20', 'V1', (map_name or 'unknown').lower()), True
