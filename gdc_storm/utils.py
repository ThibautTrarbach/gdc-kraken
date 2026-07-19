# Fonctions utilitaires
import re

from .models import Mission


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
    matches = [
        m
        for m in Mission.objects.filter(map__iexact=map_normalized)
        if (m.name or '').casefold() == name_key
    ]
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
    import re
    allowed_types = '|'.join([choice[0] for choice in Mission.TYPE_CHOICES])
    # Autorise lettres, chiffres, ponctuation, accents, caractères spéciaux clavier qwerty/azerty
    pattern = rf"^(CPC-({allowed_types})\[(\d{{2,3}})\]-[\w\d\s\-\_\(\)@#%&'éèàùâêîôûäëïöüçÉÈÀÙÂÊÎÔÛÄËÏÖÜÇ]+)-([Vv]\d+)\.(.+)\.pbo$"
    match = re.match(pattern, filename)
    if not match:
        return None
    return match.groups()


# LEGACY ONLY - Fonctions utilitaires pour l'import massif de missions .pbo
def legacy_parse_mission_filename(filename):
    # Expected output:
    # mission_name, mission_type, max_players, version, map_name
    import re
    allowed_types = '|'.join([choice[0] for choice in Mission.TYPE_CHOICES])
    # Autorise lettres, chiffres, ponctuation, accents, caractères spéciaux clavier qwerty/azerty
    #pattern = rf"^(CPC-({allowed_types})\[(\d{{2,3}})\]-?[\w\d\s\-\_\(\)@#%&'éèàùâêîôûäëïöüçÉÈÀÙÂÊÎÔÛÄËÏÖÜÇ]+)(?:[-_]([Vv]\d+))?\.(.+)\.pbo$"
    pattern = rf"^(CPC-({allowed_types})\[(\d{{2,3}})\]-?[\w\d\s\-\_\(\)@#%&'éèàùâêîôûäëïöüçÉÈÀÙÂÊÎÔÛÄËÏÖÜÇ]+)\.(.+)\.pbo$"
    match = re.match(pattern, filename)
    if not match:
        return None
    mission_name, mission_type, max_players, map_name = match.groups()
    
    pattern = rf"^(CPC-({allowed_types})\[(\d{{2,3}})\]-[\w\d\s\-\_\(\)@#%&'éèàùâêîôûäëïöüçÉÈÀÙÂÊÎÔÛÄËÏÖÜÇ]+)-([Vv]\d+)"
    match = re.match(pattern, filename)
    if not match:
        # Version non fournie, on met V1 par défaut
        version = "V1"
    else:
        groups = list(match.groups())
        mission_name = groups[0]
        version = groups[3]
    return mission_name, mission_type, max_players, version, map_name


def recup_parse_mission_filename(filename):
    """
    Temporaire récup : pas de rejet sur le nom.
    Essaie le parse strict, sinon legacy, sinon stem/map avec défauts.
    Retourne ((name, type, max_players, version, map), relaxed) ou None.
    """
    import os

    if not filename or not str(filename).lower().endswith('.pbo'):
        return None

    strict = parse_mission_filename(filename)
    if strict:
        return strict, False

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
