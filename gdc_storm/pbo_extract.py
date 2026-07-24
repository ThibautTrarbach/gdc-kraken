import json
import logging
import math
import os
import re
import uuid
from html import escape as html_escape
from pathlib import Path

# Briefing Arma : balises utiles au rendu, sans event handlers / scripts.
BRIEFING_ALLOWED_TAGS = frozenset({
    'br', 'b', 'u', 'i', 'strong', 'em', 'p', 'h1', 'h2', 'h3', 'h4',
    'a', 'img', 'font', 'span', 'div', 'ul', 'ol', 'li',
})
BRIEFING_ALLOWED_ATTRIBUTES = {
    'a': ['href', 'title', 'name', 'class', 'data-marker-name'],
    'img': ['src', 'alt', 'width', 'height'],
    'font': ['color', 'size', 'face'],
}
BRIEFING_ALLOWED_PROTOCOLS = frozenset({'http', 'https', 'mailto'})

_MARKER_TAG_RE = re.compile(
    r'<\s*marker\b([^>]*)>(.*?)<\s*/\s*marker\s*>',
    re.DOTALL | re.IGNORECASE,
)
_MARKER_NAME_ATTR_RE = re.compile(
    r"""name\s*=\s*(?:'([^']*)'|"([^"]*)"|([^\s>'"]+))""",
    re.IGNORECASE,
)


def convert_briefing_marker_tags(html: str) -> str:
    """
    Convertit les balises Arma <marker name='id'>texte</marker> :
    - avec name → lien cliquable vers la carte
    - sans name → gras souligné (comme avant)
    """
    if not html:
        return html or ''

    def repl(match: re.Match) -> str:
        attrs = match.group(1) or ''
        text = match.group(2)
        name_match = _MARKER_NAME_ATTR_RE.search(attrs)
        name = ''
        if name_match:
            name = (name_match.group(1) or name_match.group(2) or name_match.group(3) or '').strip()
        if name:
            safe_name = html_escape(name, quote=True)
            return (
                f'<a class="briefing-marker-link" data-marker-name="{safe_name}" '
                f'href="#mission-map" title="Voir sur la carte">{text}</a>'
            )
        return f'<u><b>{text}</b></u>'

    # Plusieurs passes pour les balises imbriquees.
    previous = None
    current = html
    while previous != current:
        previous = current
        current = _MARKER_TAG_RE.sub(repl, current)
    return current


def sanitize_briefing_html(html):
    """Nettoie le HTML de briefing (XSS) en conservant le formatage Arma usuel."""
    if not html:
        return html or ''
    import bleach

    converted = convert_briefing_marker_tags(html)
    return bleach.clean(
        converted,
        tags=BRIEFING_ALLOWED_TAGS,
        attributes=BRIEFING_ALLOWED_ATTRIBUTES,
        protocols=BRIEFING_ALLOWED_PROTOCOLS,
        strip=True,
    )

def is_sqm_binarized(pbo):
    """Retourne True si mission.sqm est binarisé (ne commence pas par 'version'), False sinon, None si absent."""
    try:
        sqm_file = pbo['mission.sqm']
    except KeyError:
        return None  # Pas de mission.sqm
    sqm_content = sqm_file.data.decode('utf-8', errors='replace')
    if not re.match(r'^version', sqm_content.strip()):
        return True
    return False


def extract_mission_data_from_pbo(pbo):
    """
    Extrait les champs author, onLoadMission, overviewText, loadScreen, minPlayers
    depuis description.ext (insensible à la casse) puis mission.sqm (dans ScenarioData) si manquant.
    Retourne (data_dict, problems[]).
    """
    fields = [
        'author', 'onLoadMission', 'overviewText', 'loadScreen', 'minPlayers'
    ]
    data = {k: None for k in fields}
    problems = []
    # --- Extraction depuis description.ext (insensible à la casse) ---
    try:
        description_ext_file = pbo['description.ext']
        description_ext_content = description_ext_file.data.decode('utf-8')
        regexes = {
            'author': r'author\s*=\s*"([^"]+)",?',
            'onLoadMission': r'onloadmission\s*=\s*"([^"]+)",?',
            'overviewText': r'overviewtext\s*=\s*"([^"]+)",?',
            'loadScreen': r'loadscreen\s*=\s*"([^"]+)",?',
            'minPlayers': r'minplayers\s*=\s*(\d+)',
        }
        for field, regex in regexes.items():
            match = re.search(regex, description_ext_content, re.IGNORECASE)
            if match:
                data[field] = match.group(1)
    except KeyError:
        description_ext_content = None
    except Exception as e:
        problems.append(f"Erreur lors de la lecture de description.ext : {e}")

    # --- Extraction depuis mission.sqm (uniquement dans class ScenarioData) si manquant ---
    try:
        sqm_file = pbo['mission.sqm']
        sqm_content = sqm_file.data.decode('utf-8', errors='replace')
        scenario_match = re.search(r'class ScenarioData\s*\{([^}]*)\}', sqm_content, re.DOTALL | re.IGNORECASE)
        if scenario_match:
            block = scenario_match.group(1)
            # author, onLoadMission, overviewText, loadScreen
            if not data['author']:
                match = re.search(r'author\s*=\s*"([^"]+)",?', block, re.IGNORECASE)
                if match:
                    data['author'] = match.group(1)
            if not data['onLoadMission']:
                match = re.search(r'onloadmission\s*=\s*"([^"]+)",?', block, re.IGNORECASE)
                if match:
                    data['onLoadMission'] = match.group(1)
            if not data['overviewText']:
                match = re.search(r'overviewtext\s*=\s*"([^"]+)",?', block, re.IGNORECASE)
                if match:
                    data['overviewText'] = match.group(1)
            if not data['loadScreen']:
                match = re.search(r'loadscreen\s*=\s*"([^"]+)",?', block, re.IGNORECASE)
                if match:
                    data['loadScreen'] = match.group(1)
            # minPlayers dans class Header à l'intérieur de ScenarioData
            if not data['minPlayers']:
                match = re.search(r'minplayers\s*=\s*(\d+)', block, re.IGNORECASE)
                if match:
                    data['minPlayers'] = match.group(1)
    except KeyError:
        sqm_content = None
    except Exception as e:
        problems.append(f"Erreur lors de la lecture de mission.sqm : {e}")

    # Valeur par défaut pour les champs optionnels
    if not data['onLoadMission']:
        data['onLoadMission'] = 'Non renseigné'
    if not data['overviewText']:
        data['overviewText'] = 'Non renseigné'
    if not data['author']:
        data['author'] = 'Non renseigné'

    return data, problems


def extract_briefing_from_pbo(pbo):
    """
    Cherche tous les fichiers briefing.sqf dans le pbo (racine ou sous-dossiers),
    extrait tous les éléments de type player createDiaryRecord ["Diary", ["BriefingItemName", "BriefingItemContent"]];
    Remplace les balises <marker name='id'>…</marker> par des liens vers la carte
    (ou gras souligné si pas de name).
    Extrait les images <img image='Chemin'>, les sauvegarde dans missions/loadscreens/briefing/ et adapte le chemin en src local.
    Supprime les lignes vides en fin de chaque élément de contenu.
    Retourne une liste de dicts : [{"name": ..., "content": ...}], dans l'ordre inverse d'apparition.
    """
    import re as _re
    import os
    from django.conf import settings
    from django.core.files.storage import default_storage
    import uuid
    briefing_items = []
    image_paths = []
    for pbo_item in pbo:
        if pbo_item.filename.lower().endswith('briefing.sqf'):
            try:
                content = pbo_item.data.decode('utf-8', errors='replace')
                # Regex robuste pour plusieurs records multiline
                for match in re.finditer(r'player\s+createDiaryRecord\s*\[\s*"Diary"\s*,\s*\[\s*"([^"]+)"\s*,\s*"((?:[^"\\]|\\.|\n)*?)"\s*]\s*]\s*;', content, re.DOTALL|re.MULTILINE):
                    name, item_content = match.group(1), match.group(2)
                    # Extraction et remplacement des images
                    def img_save_repl(m):
                        attrs = m.group(1)
                        img_path_match = _re.search(r"image\s*=\s*'([^']+)'", attrs)
                        if not img_path_match:
                            return ''
                        img_path = img_path_match.group(1)
                        # Recherche du fichier image dans le pbo
                        try:
                            img_entry = pbo[img_path]
                        except Exception:
                            return ''  # Image non trouvée dans le pbo
                        ext = os.path.splitext(img_path)[1].lower()
                        if ext not in ['.jpg', '.jpeg', '.png']:
                            return ''
                        # Sauvegarde de l'image dans le dossier missions/loadscreens/briefing/
                        img_filename = os.path.join(settings.MISSIONS_IMAGES_STORAGE_PATH, 'briefing', f"{uuid.uuid4()}{ext}")
                        os.makedirs(os.path.join(default_storage.location, settings.MISSIONS_IMAGES_STORAGE_PATH, 'briefing'), exist_ok=True)
                        with default_storage.open(img_filename, 'wb') as imgfile:
                            imgfile.write(img_entry.data)
                        image_paths.append(img_filename)
                        # Remplace image='...' par src='...' et conserve les autres attributs
                        attrs = _re.sub(r"image\s*=\s*'([^']+)'", lambda x: f"src='/media/{img_filename}'", attrs)
                        return f"<img{attrs}>"
                    item_content = _re.sub(r"<img([^>]*)>", img_save_repl, item_content)
                    # Suppression des lignes vides en fin de contenu
                    item_content = item_content.rstrip('\n').rstrip('\r')
                    item_content = sanitize_briefing_html(item_content)
                    briefing_items.append({"name": name, "content": item_content})
            except Exception as e:
                logging.error(f"Erreur lors de la lecture de {pbo_item.filename} dans le pbo : {e}")
    return list(reversed(briefing_items)), image_paths


_MARKER_BLOCK_RE = re.compile(
    r'dataType\s*=\s*"Marker"\s*;(.*?)(?=class\s+Item\d+\s*\{|dataType\s*=\s*"(?!Marker)|\Z)',
    re.DOTALL,
)
_MARKER_POSITION_RE = re.compile(r'position\[\]\s*=\s*\{([^}]+)\}')
_MARKER_STRING_FIELD_RE = re.compile(r'(\w+)\s*=\s*"([^"]*)"')
_MARKER_COLOR_ARRAY_RE = re.compile(r'color\[\]\s*=\s*\{([^}]+)\}')
_MARKER_ALPHA_FIELD_RE = re.compile(r'alpha\s*=\s*([\d.+-]+)\s*;?', re.IGNORECASE)
_MARKER_SIZE2_RE = re.compile(r'size2\[\]\s*=\s*\{([^}]+)\}', re.IGNORECASE)
_MARKER_ANGLES_YAW_RE = re.compile(
    r'angles\[\]\s*=\s*\{[^,]*,\s*([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)\s*,',
    re.IGNORECASE,
)

# Seul le type "empty" reste invisible (placeholder Eden sans rendu).
# moduleCoverMap / objectMarker : marqueurs techniques de modules Eden.
_HIDDEN_MARKER_TYPES = frozenset({'empty', 'modulecovermap', 'objectmarker'})

# Noms de spawn joueur Eden (respawn_west, spawn_east, …).
_SPAWN_NAME_RE = re.compile(
    r'^(?:respawn|spawn)'
    r'(?:[_-]?(?:west|east|guer|guerrila|independent|ind|civ|civilian|blufor|blu|opfor|op))?'
    r'\d*$',
    re.IGNORECASE,
)
_SPAWN_NAME_ALT_RE = re.compile(r'^spawn(?:point|marker)?\d*$', re.IGNORECASE)

# Marqueurs modules / IA / waypoints editeur.
_AI_LOGIC_NAME_RE = re.compile(
    r'^(?:bis_|zen_|module|hc_|wp\d|wp[_-]|waypoint|ace_|ai[_-]|ia[_-])',
    re.IGNORECASE,
)

# Hide Terrain Objects / classes module Arma (type, nom ou icône).
_HIDE_TERRAIN_RE = re.compile(
    r'hideterrain|modulehideterrain|modulecovermap',
    re.IGNORECASE,
)
_MODULE_CLASS_TYPE_RE = re.compile(r'^module\w+_f$', re.IGNORECASE)

_LOGIC_BLOCK_RE = re.compile(
    r'dataType\s*=\s*"Logic"\s*;(.*?)(?=class\s+Item\d+\s*\{|dataType\s*=\s*"(?!Logic)|\Z)',
    re.DOTALL,
)
_LOGIC_TYPE_RE = re.compile(r'\btype\s*=\s*"([^"]+)"', re.IGNORECASE)
_HIDE_TERRAIN_LOGIC_TYPES = frozenset({
    'modulehideterrainobjects_f',
    'modulehideterrainobjects',
})
# Tolérance (m) pour associer une zone Marker à un module HideTerrainObjects.
_HIDE_TERRAIN_COLOCATE_M = 3.0

_TRANSPARENT_ALPHA_THRESHOLD = 0.01

# --- Analyse SQF : marqueurs masqués au démarrage (mission making) ---
_EXEC_VM_SQF_RE = re.compile(
    r'(?:execVM|preprocessFile(?:LineNumbers)?)\s+["\']([^"\']+\.sqf)["\']',
    re.IGNORECASE,
)
_STARTUP_SQF_NAME_RE = re.compile(
    r'(?:^|/)(init(?:server|playerlocal|playerserver|post|pre)?|oninit)\.sqf$',
    re.IGNORECASE,
)
_SQf_MARKER_STRINGS_IN_ARRAY_RE = re.compile(r'["\']([^"\']+)["\']')
_SQf_OOP_SET_MARKER_ALPHA_RE = re.compile(
    r'["\']([^"\']+)["\']\s*setMarkerAlpha(?:Local)?\s+([\d.]+)',
    re.IGNORECASE,
)
_SQf_SET_MARKER_ALPHA_ARRAY_RE = re.compile(
    r'setMarkerAlpha(?:Local)?\s*\[\s*["\']([^"\']+)["\']\s*,\s*([\d.]+)\s*\]',
    re.IGNORECASE,
)
_SQf_DELETE_MARKER_RE = re.compile(
    r'deleteMarker(?:Local)?\s+["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_SQf_FOREACH_SET_MARKER_ALPHA_RE = re.compile(
    r'setMarkerAlpha(?:Local)?\s*\[\s*_x\s*,\s*([\d.]+)\s*\][^}]*\}\s*forEach\s*\[([^\]]+)\]',
    re.IGNORECASE | re.DOTALL,
)
_SQf_FOREACH_X_SET_MARKER_ALPHA_RE = re.compile(
    r'\{\s*_x\s+setMarkerAlpha(?:Local)?\s+([\d.]+)\s*\}\s*forEach\s*\[([^\]]+)\]',
    re.IGNORECASE,
)
_SQf_FOREACH_DELETE_MARKER_RE = re.compile(
    r'\{\s*deleteMarker(?:Local)?\s+_x\s*\}\s*forEach\s*\[([^\]]+)\]',
    re.IGNORECASE,
)
_SQf_SET_MARKER_SIZE_ZERO_RE = re.compile(
    r'setMarkerSize(?:Local)?\s*\[\s*["\']([^"\']+)["\']\s*,\s*\[\s*0\s*,\s*0\s*\]\s*\]',
    re.IGNORECASE,
)
_SQf_OOP_SET_MARKER_TYPE_EMPTY_RE = re.compile(
    r'["\']([^"\']+)["\']\s*setMarkerType(?:Local)?\s+["\']Empty["\']',
    re.IGNORECASE,
)
_SQf_SET_MARKER_TYPE_EMPTY_ARRAY_RE = re.compile(
    r'setMarkerType(?:Local)?\s*\[\s*["\']([^"\']+)["\']\s*,\s*["\']Empty["\']\s*\]',
    re.IGNORECASE,
)
_SQf_VAR_SET_MARKER_ALPHA_RE = re.compile(
    r'(_\w+)\s+setMarkerAlpha(?:Local)?\s+([\d.]+)',
    re.IGNORECASE,
)
_SQf_VAR_DELETE_MARKER_RE = re.compile(
    r'(_\w+)\s+deleteMarker(?:Local)?',
    re.IGNORECASE,
)
_SQf_VAR_SET_MARKER_TYPE_EMPTY_RE = re.compile(
    r'(_\w+)\s+setMarkerType(?:Local)?\s+["\']Empty["\']',
    re.IGNORECASE,
)
_SQf_FOREACH_ALLMAPMARKERS_BLOCK_RE = re.compile(
    r'\{([^{}]*(?:\{[^{}]*\}[^{}]*)*)\}\s*forEach\s+allMapMarkers\b',
    re.IGNORECASE | re.DOTALL,
)
_DESCRIPTION_EXT_SQF_FILE_RE = re.compile(r'\bfile\s*=\s*"([^"]+\.sqf)"', re.IGNORECASE)
_DESCRIPTION_EXT_INLINE_INIT_RE = re.compile(
    r'\binit\s*=\s*"((?:[^"]|"")*)"',
    re.IGNORECASE,
)
_SQM_INIT_ATTR_RE = re.compile(r'\binit\s*=\s*', re.IGNORECASE)
_SQM_SCENARIO_INIT_EXPRESSION_RE = re.compile(
    r'property\s*=\s*"Init"\s*;.*?expression\s*=\s*"((?:[^"]|"")*)"',
    re.IGNORECASE | re.DOTALL,
)
# Configuration marqueurs (forme / taille) appliquée par scripts au démarrage
_SQf_OOP_SET_MARKER_SHAPE_RE = re.compile(
    r'["\']([^"\']+)["\']\s*setMarkerShape(?:Local)?\s+["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_SQf_SET_MARKER_SHAPE_ARRAY_RE = re.compile(
    r'setMarkerShape(?:Local)?\s*\[\s*["\']([^"\']+)["\']\s*,\s*["\']([^"\']+)["\']\s*\]',
    re.IGNORECASE,
)
_SQf_OOP_SET_MARKER_SIZE_RE = re.compile(
    r'["\']([^"\']+)["\']\s*setMarkerSize(?:Local)?\s+\[\s*([\d.]+)\s*,\s*([\d.]+)\s*\]',
    re.IGNORECASE,
)
_SQf_SET_MARKER_SIZE_ARRAY_RE = re.compile(
    r'setMarkerSize(?:Local)?\s*\[\s*["\']([^"\']+)["\']\s*,\s*\[\s*([\d.]+)\s*,\s*([\d.]+)\s*\]\s*\]',
    re.IGNORECASE,
)
_SQf_OOP_SET_MARKER_DIR_RE = re.compile(
    r'["\']([^"\']+)["\']\s*setMarkerDir(?:Local)?\s+([\d.]+)',
    re.IGNORECASE,
)
_SQf_SET_MARKER_DIR_ARRAY_RE = re.compile(
    r'setMarkerDir(?:Local)?\s*\[\s*["\']([^"\']+)["\']\s*,\s*([\d.]+)\s*\]',
    re.IGNORECASE,
)
_SQf_OOP_SET_MARKER_BRUSH_RE = re.compile(
    r'["\']([^"\']+)["\']\s*setMarkerBrush(?:Local)?\s+["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_SQf_OOP_SET_MARKER_COLOR_RE = re.compile(
    r'["\']([^"\']+)["\']\s*setMarkerColor(?:Local)?\s+["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_SQf_OOP_SET_MARKER_TEXT_RE = re.compile(
    r'["\']([^"\']+)["\']\s*setMarkerText(?:Local)?\s+["\']([^"\']*)["\']',
    re.IGNORECASE,
)
_SQf_CREATE_MARKER_RE = re.compile(
    r'createMarker(?:Local)?\s*\[\s*["\']([^"\']+)["\']\s*,\s*\[([^\]]+)\]\s*\]',
    re.IGNORECASE,
)
_SQf_VAR_CREATE_MARKER_RE = re.compile(
    r'(_\w+)\s*=\s*createMarker(?:Local)?\s*\[\s*["\']([^"\']+)["\']\s*,\s*\[([^\]]+)\]\s*\]',
    re.IGNORECASE,
)
_SQf_VAR_SET_MARKER_SHAPE_RE = re.compile(
    r'(_\w+)\s+setMarkerShape(?:Local)?\s+["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_SQf_VAR_SET_MARKER_SIZE_RE = re.compile(
    r'(_\w+)\s+setMarkerSize(?:Local)?\s+\[\s*([\d.]+)\s*,\s*([\d.]+)\s*\]',
    re.IGNORECASE,
)
_SQf_VAR_SET_MARKER_DIR_RE = re.compile(
    r'(_\w+)\s+setMarkerDir(?:Local)?\s+([\d.]+)',
    re.IGNORECASE,
)
_SQf_VAR_SET_MARKER_BRUSH_RE = re.compile(
    r'(_\w+)\s+setMarkerBrush(?:Local)?\s+["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_SQf_VAR_SET_MARKER_COLOR_RE = re.compile(
    r'(_\w+)\s+setMarkerColor(?:Local)?\s+["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_SQf_VAR_SET_MARKER_TEXT_RE = re.compile(
    r'(_\w+)\s+setMarkerText(?:Local)?\s+["\']([^"\']*)["\']',
    re.IGNORECASE,
)
_SQf_SHAPE_TO_ZONE_TYPE = {
    'ellipse': 'ellipse',
    'rectangle': 'rectangle',
}


def _sqm_unescape_string(value: str) -> str:
    return (value or '').replace('""', '"')


def _extract_sqm_quoted_string_literals(content: str, start: int) -> tuple[str, int]:
    """Lit une ou plusieurs chaînes SQM concaténées ("a" \\n "b") à partir de start."""
    pos = start
    parts: list[str] = []
    while pos < len(content):
        gap = re.match(r'\s*(?:\\\s*n\s*)?', content[pos:])
        if not gap:
            break
        pos += gap.end()
        if pos >= len(content) or content[pos] != '"':
            break
        pos += 1
        chunk: list[str] = []
        while pos < len(content):
            char = content[pos]
            if char == '"':
                if pos + 1 < len(content) and content[pos + 1] == '"':
                    chunk.append('"')
                    pos += 2
                    continue
                pos += 1
                break
            chunk.append(char)
            pos += 1
        parts.append(''.join(chunk))
    return _sqm_unescape_string('\n'.join(parts)), pos


def _extract_sqm_startup_scripts(sqm_content: str) -> str:
    """
    Extrait le SQF embarqué dans mission.sqm au démarrage :
    attribut init= des entités et expression Init des attributs scénario Eden.
    """
    if not sqm_content:
        return ''

    blocks: list[str] = []
    for match in _SQM_INIT_ATTR_RE.finditer(sqm_content):
        literal, _ = _extract_sqm_quoted_string_literals(sqm_content, match.end())
        if literal.strip():
            blocks.append(literal)

    for match in _SQM_SCENARIO_INIT_EXPRESSION_RE.finditer(sqm_content):
        literal = _sqm_unescape_string(match.group(1))
        if literal.strip():
            blocks.append(literal)

    return '\n'.join(blocks)


def _read_mission_sqm_text(pbo) -> str | None:
    try:
        sqm_file = pbo['mission.sqm']
    except KeyError:
        return None
    if is_sqm_binarized(pbo):
        return None
    return sqm_file.data.decode('utf-8', errors='replace')


def _normalize_pbo_script_path(path: str) -> str:
    return (path or '').replace('\\', '/').lstrip('./')


def _sqf_marker_names_in_array(array_content: str) -> list[str]:
    return _SQf_MARKER_STRINGS_IN_ARRAY_RE.findall(array_content or '')


def _sqf_marker_visibility_state(content: str) -> dict[str, bool]:
    """
    Analyse un bloc SQF et retourne name -> visible (True/False).
    Dernière opération par marqueur dans le fichier l'emporte.
    """
    states: dict[str, bool] = {}
    var_to_name: dict[str, str] = {}
    if not content:
        return states

    for match in _SQf_VAR_CREATE_MARKER_RE.finditer(content):
        var_to_name[match.group(1)] = match.group(2)

    def resolve(name_or_var: str) -> str:
        token = (name_or_var or '').strip()
        return var_to_name.get(token, token)

    def apply(name: str, visible: bool) -> None:
        clean = resolve(name)
        if clean and clean != '_x':
            states[clean] = visible

    for match in _SQf_OOP_SET_MARKER_ALPHA_RE.finditer(content):
        try:
            alpha = float(match.group(2))
        except ValueError:
            continue
        apply(match.group(1), alpha > _TRANSPARENT_ALPHA_THRESHOLD)

    for match in _SQf_SET_MARKER_ALPHA_ARRAY_RE.finditer(content):
        try:
            alpha = float(match.group(2))
        except ValueError:
            continue
        apply(match.group(1), alpha > _TRANSPARENT_ALPHA_THRESHOLD)

    for match in _SQf_VAR_SET_MARKER_ALPHA_RE.finditer(content):
        try:
            alpha = float(match.group(2))
        except ValueError:
            continue
        apply(match.group(1), alpha > _TRANSPARENT_ALPHA_THRESHOLD)

    for match in _SQf_DELETE_MARKER_RE.finditer(content):
        apply(match.group(1), False)

    for match in _SQf_VAR_DELETE_MARKER_RE.finditer(content):
        apply(match.group(1), False)

    for match in _SQf_OOP_SET_MARKER_TYPE_EMPTY_RE.finditer(content):
        apply(match.group(1), False)

    for match in _SQf_SET_MARKER_TYPE_EMPTY_ARRAY_RE.finditer(content):
        apply(match.group(1), False)

    for match in _SQf_VAR_SET_MARKER_TYPE_EMPTY_RE.finditer(content):
        apply(match.group(1), False)

    for match in _SQf_FOREACH_SET_MARKER_ALPHA_RE.finditer(content):
        try:
            alpha = float(match.group(1))
        except ValueError:
            continue
        visible = alpha > _TRANSPARENT_ALPHA_THRESHOLD
        for name in _sqf_marker_names_in_array(match.group(2)):
            apply(name, visible)

    for match in _SQf_FOREACH_X_SET_MARKER_ALPHA_RE.finditer(content):
        try:
            alpha = float(match.group(1))
        except ValueError:
            continue
        visible = alpha > _TRANSPARENT_ALPHA_THRESHOLD
        for name in _sqf_marker_names_in_array(match.group(2)):
            apply(name, visible)

    for match in _SQf_FOREACH_DELETE_MARKER_RE.finditer(content):
        for name in _sqf_marker_names_in_array(match.group(1)):
            apply(name, False)

    for match in _SQf_SET_MARKER_SIZE_ZERO_RE.finditer(content):
        apply(match.group(1), False)

    return states


def _sqf_marker_name_substring_hide_rules(content: str) -> list[str]:
    """
    Règles du type : { if (\"mkr\" in _x) then { _x setMarkerAlpha 0 }; } forEach allMapMarkers;
    Retourne les sous-chaînes de nom à masquer (ex. \"mkr\" → mkr_spawnHeli).
    """
    rules: list[str] = []
    if not content:
        return rules
    for match in _SQf_FOREACH_ALLMAPMARKERS_BLOCK_RE.finditer(content):
        body = match.group(1)
        if not re.search(r'setMarkerAlpha(?:Local)?\s+0\b', body, re.IGNORECASE):
            continue
        in_match = re.search(r'["\']([^"\']+)["\']\s+in\s+_x\b', body, re.IGNORECASE)
        if in_match:
            rules.append(in_match.group(1))
            continue
        find_match = re.search(r'_x\s+find\s+["\']([^"\']+)["\']', body, re.IGNORECASE)
        if find_match:
            rules.append(find_match.group(1))
    return rules


def _pbo_sqf_path_index(pbo) -> dict[str, str]:
    index: dict[str, str] = {}
    for pbo_item in pbo:
        filename = getattr(pbo_item, 'filename', '') or ''
        if not filename.lower().endswith('.sqf'):
            continue
        normalized = _normalize_pbo_script_path(filename)
        index[normalized.lower()] = normalized
        basename = normalized.rsplit('/', 1)[-1].lower()
        if basename not in index:
            index[basename] = normalized
    return index


def _resolve_pbo_sqf_path(sqf_index: dict[str, str], reference: str) -> str | None:
    ref = _normalize_pbo_script_path(reference)
    if not ref:
        return None
    direct = sqf_index.get(ref.lower())
    if direct:
        return direct
    basename = ref.rsplit('/', 1)[-1].lower()
    return sqf_index.get(basename)


def _read_pbo_text_file(pbo, path: str) -> str | None:
    normalized = _normalize_pbo_script_path(path)
    try:
        if normalized == 'description.ext':
            return pbo['description.ext'].data.decode('utf-8', errors='replace')
    except KeyError:
        pass
    sqf_index = _pbo_sqf_path_index(pbo)
    resolved = _resolve_pbo_sqf_path(sqf_index, normalized)
    if not resolved:
        return None
    for pbo_item in pbo:
        if _normalize_pbo_script_path(getattr(pbo_item, 'filename', '')) == resolved:
            return pbo_item.data.decode('utf-8', errors='replace')
    return None


def _collect_startup_script_paths(pbo) -> list[str]:
    """
    Scripts exécutés au démarrage : init*.sqf, description.ext,
    et fichiers .sqf référencés par execVM / preprocessFile (chaîne incluse).
    """
    sqf_index = _pbo_sqf_path_index(pbo)
    startup: list[str] = []
    for path in sqf_index.values():
        basename = path.rsplit('/', 1)[-1]
        if _STARTUP_SQF_NAME_RE.search(path) or basename.lower() == 'init.sqf':
            startup.append(path)

    queue: list[str] = list(dict.fromkeys(startup))
    try:
        pbo['description.ext']
        queue.append('description.ext')
    except KeyError:
        pass

    try:
        desc_content = pbo['description.ext'].data.decode('utf-8', errors='replace')
        for match in _DESCRIPTION_EXT_SQF_FILE_RE.finditer(desc_content):
            resolved = _resolve_pbo_sqf_path(sqf_index, match.group(1))
            if resolved and resolved not in queue:
                queue.append(resolved)
    except KeyError:
        desc_content = None
    except Exception:
        desc_content = None

    sqm_content = _read_mission_sqm_text(pbo)
    if sqm_content:
        for match in _EXEC_VM_SQF_RE.finditer(_extract_sqm_startup_scripts(sqm_content)):
            resolved = _resolve_pbo_sqf_path(sqf_index, match.group(1))
            if resolved and resolved not in queue:
                queue.append(resolved)

    visited: set[str] = set()
    ordered: list[str] = []
    while queue:
        path = queue.pop(0)
        if path in visited:
            continue
        visited.add(path)
        ordered.append(path)
        content = _read_pbo_text_file(pbo, path)
        if not content:
            continue
        for match in _EXEC_VM_SQF_RE.finditer(content):
            resolved = _resolve_pbo_sqf_path(sqf_index, match.group(1))
            if resolved and resolved not in visited:
                queue.append(resolved)
    return ordered


def extract_script_hidden_markers_from_pbo(
    pbo,
    marker_names: list[str] | None = None,
) -> frozenset[str]:
    """
    Marqueurs Eden masqués au démarrage via SQF (setMarkerAlpha 0, deleteMarker, …).
    Analyse init*.sqf, description.ext, mission.sqm (init embarqué), execVM chain.
    Gère aussi les boucles forEach allMapMarkers avec filtre (\"mkr\" in _x).
    """
    net_visible: dict[str, bool] = {}
    substring_rules: list[str] = []

    def merge_states(states: dict[str, bool]) -> None:
        for name, visible in states.items():
            net_visible[name] = visible

    for content in _iter_startup_sqf_contents(pbo):
        merge_states(_sqf_marker_visibility_state(content))
        for rule in _sqf_marker_name_substring_hide_rules(content):
            if rule not in substring_rules:
                substring_rules.append(rule)

    hidden: set[str] = {
        name for name, visible in net_visible.items()
        if not visible and name and name != '_x'
    }
    if marker_names and substring_rules:
        for name in marker_names:
            if name and any(rule in name for rule in substring_rules):
                hidden.add(name)
    return frozenset(hidden)


def filter_markers_hidden_by_script(
    markers: list[dict],
    script_hidden: frozenset[str],
) -> tuple[list[dict], int]:
    if not script_hidden:
        return markers, 0
    kept: list[dict] = []
    hidden_count = 0
    for marker in markers:
        name = (marker.get('name') or '').strip()
        if name and name in script_hidden:
            hidden_count += 1
            continue
        kept.append(marker)
    return kept, hidden_count


def _parse_sqf_marker_position(position_expr: str) -> tuple[float, float] | None:
    parts = [p.strip() for p in (position_expr or '').split(',')]
    try:
        if len(parts) >= 3:
            return float(parts[0]), float(parts[2])
        if len(parts) >= 2:
            return float(parts[0]), float(parts[1])
    except ValueError:
        return None
    return None


def _sqf_zone_type_from_shape(shape: str) -> str:
    return _SQf_SHAPE_TO_ZONE_TYPE.get((shape or '').strip().lower(), '')


def _sqf_marker_config_overrides(content: str) -> dict[str, dict]:
    """
    Lit setMarkerShape/Size/Dir/Brush/Color/Text et createMarker dans un bloc SQF.
    Dernière commande par marqueur l'emporte (ordre d'exécution simulé).
    """
    overrides: dict[str, dict] = {}
    var_to_name: dict[str, str] = {}
    if not content:
        return overrides

    def resolve(name_or_var: str) -> str:
        token = (name_or_var or '').strip()
        return var_to_name.get(token, token)

    def cfg(name: str) -> dict:
        clean = (name or '').strip()
        if not clean:
            return {}
        if clean not in overrides:
            overrides[clean] = {}
        return overrides[clean]

    def apply_shape(name: str, shape: str) -> None:
        clean = resolve(name)
        zone_type = _sqf_zone_type_from_shape(shape)
        if zone_type:
            cfg(clean)['type'] = zone_type

    def apply_size(name: str, a_raw: str, b_raw: str) -> None:
        clean = resolve(name)
        try:
            a_val = float(a_raw)
            b_val = float(b_raw)
        except ValueError:
            return
        target = cfg(clean)
        target['a'] = a_val
        target['b'] = b_val

    def apply_dir(name: str, angle_raw: str) -> None:
        clean = resolve(name)
        try:
            cfg(clean)['angle'] = float(angle_raw)
        except ValueError:
            return

    def apply_brush(name: str, brush: str) -> None:
        clean = resolve(name)
        if brush:
            cfg(clean)['brush'] = brush

    def apply_color(name: str, color: str) -> None:
        clean = resolve(name)
        if color:
            cfg(clean)['color'] = color

    def apply_text(name: str, text: str) -> None:
        clean = resolve(name)
        if text is not None:
            cfg(clean)['text'] = text

    def apply_create(name: str, position_expr: str) -> None:
        clean = (name or '').strip()
        pos = _parse_sqf_marker_position(position_expr)
        if not clean or not pos:
            return
        target = cfg(clean)
        target['x'] = pos[0]
        target['z'] = pos[1]
        target.setdefault('type', 'empty')

    # createMarker + alias variable → nom littéral
    for match in _SQf_VAR_CREATE_MARKER_RE.finditer(content):
        var_to_name[match.group(1)] = match.group(2)
        apply_create(match.group(2), match.group(3))

    for match in _SQf_CREATE_MARKER_RE.finditer(content):
        apply_create(match.group(1), match.group(2))

    for match in _SQf_OOP_SET_MARKER_SHAPE_RE.finditer(content):
        apply_shape(match.group(1), match.group(2))
    for match in _SQf_SET_MARKER_SHAPE_ARRAY_RE.finditer(content):
        apply_shape(match.group(1), match.group(2))
    for match in _SQf_VAR_SET_MARKER_SHAPE_RE.finditer(content):
        apply_shape(match.group(1), match.group(2))

    for match in _SQf_OOP_SET_MARKER_SIZE_RE.finditer(content):
        apply_size(match.group(1), match.group(2), match.group(3))
    for match in _SQf_SET_MARKER_SIZE_ARRAY_RE.finditer(content):
        apply_size(match.group(1), match.group(2), match.group(3))
    for match in _SQf_VAR_SET_MARKER_SIZE_RE.finditer(content):
        apply_size(match.group(1), match.group(2), match.group(3))

    for match in _SQf_OOP_SET_MARKER_DIR_RE.finditer(content):
        apply_dir(match.group(1), match.group(2))
    for match in _SQf_SET_MARKER_DIR_ARRAY_RE.finditer(content):
        apply_dir(match.group(1), match.group(2))
    for match in _SQf_VAR_SET_MARKER_DIR_RE.finditer(content):
        apply_dir(match.group(1), match.group(2))

    for match in _SQf_OOP_SET_MARKER_BRUSH_RE.finditer(content):
        apply_brush(match.group(1), match.group(2))
    for match in _SQf_VAR_SET_MARKER_BRUSH_RE.finditer(content):
        apply_brush(match.group(1), match.group(2))

    for match in _SQf_OOP_SET_MARKER_COLOR_RE.finditer(content):
        apply_color(match.group(1), match.group(2))
    for match in _SQf_VAR_SET_MARKER_COLOR_RE.finditer(content):
        apply_color(match.group(1), match.group(2))

    for match in _SQf_OOP_SET_MARKER_TEXT_RE.finditer(content):
        apply_text(match.group(1), match.group(2))
    for match in _SQf_VAR_SET_MARKER_TEXT_RE.finditer(content):
        apply_text(match.group(1), match.group(2))

    return overrides


def _iter_startup_sqf_contents(pbo):
    """Yield le contenu SQF de démarrage (fichiers + init embarqué mission.sqm / description.ext)."""
    seen: set[str] = set()
    for path in _collect_startup_script_paths(pbo):
        content = _read_pbo_text_file(pbo, path)
        if not content or path in seen:
            continue
        seen.add(path)
        yield content

    sqm_content = _read_mission_sqm_text(pbo)
    if sqm_content:
        embedded = _extract_sqm_startup_scripts(sqm_content)
        if embedded:
            yield embedded

    try:
        desc_content = pbo['description.ext'].data.decode('utf-8', errors='replace')
        for match in _DESCRIPTION_EXT_INLINE_INIT_RE.finditer(desc_content):
            inline = _sqm_unescape_string(match.group(1))
            if inline.strip():
                yield inline
    except KeyError:
        pass
    except Exception:
        pass


def diagnose_pbo_markers(pbo) -> dict:
    """Rapport debug : marqueurs SQM, masquages/configs SQF, résultat final."""
    binarized = is_sqm_binarized(pbo)
    sqm_markers: list[dict] = []
    sqm_problems: list[str] = []
    if binarized is True:
        sqm_problems.append('mission.sqm binarisé')
    elif binarized is False:
        try:
            sqm_content = pbo['mission.sqm'].data.decode('utf-8', errors='replace')
            sqm_markers, sqm_problems = extract_markers_from_sqm(sqm_content)
        except Exception as exc:
            sqm_problems.append(str(exc))

    hidden = extract_script_hidden_markers_from_pbo(
        pbo,
        marker_names=[(m.get('name') or '').strip() for m in sqm_markers],
    )
    configs = extract_script_marker_configs_from_pbo(pbo)
    final_markers, final_problems = extract_markers_from_pbo(pbo)

    startup_scripts = _collect_startup_script_paths(pbo)
    visibility_by_script: dict[str, dict[str, bool]] = {}
    configs_by_script: dict[str, dict] = {}
    for path in startup_scripts:
        content = _read_pbo_text_file(pbo, path)
        if not content:
            continue
        vis = _sqf_marker_visibility_state(content)
        if vis:
            visibility_by_script[path] = vis
        cfg = _sqf_marker_config_overrides(content)
        if cfg:
            configs_by_script[path] = cfg

    return {
        'startup_scripts': startup_scripts,
        'sqm_visible_count': len(sqm_markers),
        'sqm_markers': sqm_markers,
        'sqm_problems': sqm_problems,
        'script_hidden': sorted(hidden),
        'script_configs': configs,
        'visibility_by_script': visibility_by_script,
        'configs_by_script': configs_by_script,
        'final_count': len(final_markers),
        'final_markers': final_markers,
        'final_problems': final_problems,
    }


def extract_script_marker_configs_from_pbo(pbo) -> dict[str, dict]:
    merged: dict[str, dict] = {}
    for content in _iter_startup_sqf_contents(pbo):
        for name, patch in _sqf_marker_config_overrides(content).items():
            merged.setdefault(name, {}).update(patch)
    return merged


def apply_script_marker_configs(
    markers: list[dict],
    configs: dict[str, dict],
) -> tuple[list[dict], int]:
    if not configs:
        return markers, 0
    by_name = {(m.get('name') or '').strip(): m for m in markers if (m.get('name') or '').strip()}
    updated = 0
    for name, patch in configs.items():
        if name in by_name:
            marker = by_name[name]
            before_type = _normalize_marker_type(marker.get('type', ''))
            marker.update({k: v for k, v in patch.items() if v is not None})
            after_type = _normalize_marker_type(marker.get('type', ''))
            if before_type != after_type or patch.get('a') or patch.get('b'):
                updated += 1
            continue
        if 'x' not in patch or 'z' not in patch:
            continue
        new_marker = {
            'name': name,
            'text': patch.get('text', ''),
            'x': patch['x'],
            'z': patch['z'],
            'type': patch.get('type') or 'empty',
            'color': patch.get('color', ''),
        }
        for key in ('a', 'b', 'angle', 'brush', 'alpha'):
            if key in patch:
                new_marker[key] = patch[key]
        if is_marker_visible(new_marker):
            markers.append(new_marker)
            by_name[name] = new_marker
            updated += 1
    return markers, updated


def _normalize_marker_type(marker_type: str) -> str:
    raw = (marker_type or '').strip().lower()
    if raw.endswith('_noshadow'):
        raw = raw[: -len('_noshadow')]
    return raw


def _parse_marker_alpha(strings: dict[str, str], block: str) -> float | None:
    alpha_match = _MARKER_ALPHA_FIELD_RE.search(block)
    if alpha_match:
        try:
            return float(alpha_match.group(1))
        except ValueError:
            pass
    arr_match = _MARKER_COLOR_ARRAY_RE.search(block)
    if not arr_match:
        return None
    parts = [p.strip() for p in arr_match.group(1).split(',')]
    if len(parts) < 4:
        return None
    try:
        return float(parts[3])
    except ValueError:
        return None


def _marker_looks_like_hide_terrain(marker: dict) -> bool:
    """True si type/nom/icône évoquent HideTerrainObjects ou CoverMap."""
    marker_type = _normalize_marker_type(marker.get('type', ''))
    name = (marker.get('name') or '').strip()
    icon = (marker.get('icon') or '').strip()
    if marker_type and (
        _HIDE_TERRAIN_RE.search(marker_type)
        or _MODULE_CLASS_TYPE_RE.match(marker_type)
    ):
        return True
    if name and _HIDE_TERRAIN_RE.search(name):
        return True
    if icon and _HIDE_TERRAIN_RE.search(icon):
        return True
    return False


def _extract_hide_terrain_logic_zones(sqm_content: str) -> list[dict]:
    """Zones Logic ModuleHideTerrainObjects_F (centre + nom) depuis mission.sqm."""
    zones: list[dict] = []
    if not sqm_content:
        return zones
    for match in _LOGIC_BLOCK_RE.finditer(sqm_content):
        block = match.group(1)
        type_match = _LOGIC_TYPE_RE.search(block)
        if not type_match:
            continue
        logic_type = type_match.group(1).strip().lower()
        if logic_type not in _HIDE_TERRAIN_LOGIC_TYPES:
            continue
        pos_match = _MARKER_POSITION_RE.search(block)
        if not pos_match:
            continue
        parts = [p.strip() for p in pos_match.group(1).split(',')]
        if len(parts) < 3:
            continue
        try:
            x = float(parts[0])
            z = float(parts[2])
        except ValueError:
            continue
        strings = {m.group(1): m.group(2) for m in _MARKER_STRING_FIELD_RE.finditer(block)}
        zones.append({
            'name': (strings.get('name') or '').strip(),
            'x': x,
            'z': z,
        })
    return zones


def _marker_matches_hide_terrain_logic(marker: dict, hide_zones: list[dict]) -> bool:
    """True si le marqueur reprend le nom ou la position d'un module HideTerrain.

    La colocation ne s'applique qu'aux zones rectangle/ellipse **sans nom**,
    pour ne pas masquer une AO nommée placée au même endroit (ex. area_5).
    """
    if not hide_zones or not isinstance(marker, dict):
        return False
    name = (marker.get('name') or '').strip().lower()
    if name:
        for zone in hide_zones:
            zone_name = (zone.get('name') or '').strip().lower()
            if zone_name and zone_name == name:
                return True
        return False
    marker_type = _normalize_marker_type(marker.get('type', ''))
    if marker_type not in ('rectangle', 'ellipse'):
        return False
    try:
        mx = float(marker['x'])
        mz = float(marker['z'])
    except (KeyError, TypeError, ValueError):
        return False
    for zone in hide_zones:
        try:
            dx = mx - float(zone['x'])
            dz = mz - float(zone['z'])
        except (KeyError, TypeError, ValueError):
            continue
        if (dx * dx + dz * dz) <= (_HIDE_TERRAIN_COLOCATE_M * _HIDE_TERRAIN_COLOCATE_M):
            return True
    return False


def is_marker_visible(marker: dict, *, hide_terrain_zones: list[dict] | None = None) -> bool:
    """False pour marqueurs invisibles, spawns, IA/modules ou transparents."""
    if not isinstance(marker, dict):
        return False

    marker_type = _normalize_marker_type(marker.get('type', ''))
    name = (marker.get('name') or '').strip()
    name_lower = name.lower()

    if marker_type in _HIDDEN_MARKER_TYPES:
        return False

    if _marker_looks_like_hide_terrain(marker):
        return False

    if hide_terrain_zones and _marker_matches_hide_terrain_logic(marker, hide_terrain_zones):
        return False

    alpha = marker.get('alpha')
    if alpha is not None:
        try:
            if float(alpha) <= _TRANSPARENT_ALPHA_THRESHOLD:
                return False
        except (TypeError, ValueError):
            pass

    if name_lower and (_SPAWN_NAME_RE.match(name) or _SPAWN_NAME_ALT_RE.match(name)):
        return False

    if name and _AI_LOGIC_NAME_RE.match(name):
        return False

    if marker.get('hidden_by_script'):
        return False

    return True


def _parse_marker_numeric_fields(block: str, *field_names: str) -> dict[str, float]:
    if not field_names:
        return {}
    wanted = frozenset(name.lower() for name in field_names)
    pattern = re.compile(
        r'\b(' + '|'.join(re.escape(name) for name in field_names) + r')\s*=\s*'
        r'([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)\s*;?',
        re.IGNORECASE,
    )
    values: dict[str, float] = {}
    for match in pattern.finditer(block):
        key = match.group(1).lower()
        if key not in wanted or key in values:
            continue
        try:
            values[key] = float(match.group(2))
        except ValueError:
            continue
    return values


def _parse_marker_zone_dims(block: str) -> dict[str, float]:
    dims = _parse_marker_numeric_fields(block, 'a', 'b')
    if 'a' in dims and 'b' in dims:
        return dims
    size_match = _MARKER_SIZE2_RE.search(block)
    if size_match:
        parts = [p.strip() for p in size_match.group(1).split(',')]
        if len(parts) >= 2:
            try:
                if 'a' not in dims:
                    dims['a'] = float(parts[0])
                if 'b' not in dims:
                    dims['b'] = float(parts[1])
            except ValueError:
                pass
    return dims


def _parse_marker_zone_angle(block: str) -> float | None:
    nums = _parse_marker_numeric_fields(block, 'angle', 'rotation')
    if 'angle' in nums:
        return nums['angle']
    if 'rotation' in nums:
        return nums['rotation']
    angles_match = _MARKER_ANGLES_YAW_RE.search(block)
    if angles_match:
        try:
            return math.degrees(float(angles_match.group(1)))
        except ValueError:
            return None
    return None


def _parse_marker_color(strings: dict[str, str], block: str) -> str:
    color_name = strings.get('colorName', '')
    if color_name:
        return color_name
    arr_match = _MARKER_COLOR_ARRAY_RE.search(block)
    if not arr_match:
        return ''
    parts = [p.strip() for p in arr_match.group(1).split(',')]
    if len(parts) < 3:
        return ''
    try:
        r = max(0, min(255, int(round(float(parts[0]) * 255))))
        g = max(0, min(255, int(round(float(parts[1]) * 255))))
        b = max(0, min(255, int(round(float(parts[2]) * 255))))
    except ValueError:
        return ''
    return f'#{r:02x}{g:02x}{b:02x}'


def _parse_sqm_marker_block(block: str) -> dict | None:
    pos_match = _MARKER_POSITION_RE.search(block)
    if not pos_match:
        return None
    parts = [p.strip() for p in pos_match.group(1).split(',')]
    if len(parts) < 3:
        return None
    try:
        x = float(parts[0])
        z = float(parts[2])
    except ValueError:
        return None

    strings = {m.group(1): m.group(2) for m in _MARKER_STRING_FIELD_RE.finditer(block)}
    marker_type = strings.get('markerType') or strings.get('type') or ''
    marker = {
        'name': strings.get('name', ''),
        'text': strings.get('text', ''),
        'x': x,
        'z': z,
        'type': marker_type,
        'color': _parse_marker_color(strings, block),
    }
    alpha = _parse_marker_alpha(strings, block)
    if alpha is not None:
        marker['alpha'] = alpha
    for key, value in _parse_marker_zone_dims(block).items():
        marker[key] = value
    zone_angle = _parse_marker_zone_angle(block)
    if zone_angle is not None:
        marker['angle'] = zone_angle
    brush = strings.get('brush') or strings.get('fillName') or ''
    if brush:
        marker['brush'] = brush
    icon = strings.get('icon') or ''
    if icon:
        marker['icon'] = icon
    return marker


def extract_markers_from_sqm(sqm_content: str) -> tuple[list[dict], list[str]]:
    """Extrait les marqueurs Eden (dataType=\"Marker\") depuis mission.sqm texte."""
    problems: list[str] = []
    markers: list[dict] = []
    if not sqm_content:
        problems.append('mission.sqm vide ou illisible.')
        return markers, problems

    hide_terrain_zones = _extract_hide_terrain_logic_zones(sqm_content)
    skipped = 0
    invalid = 0
    total_blocks = 0
    for match in _MARKER_BLOCK_RE.finditer(sqm_content):
        total_blocks += 1
        marker = _parse_sqm_marker_block(match.group(1))
        if marker is None:
            invalid += 1
            continue
        if not is_marker_visible(marker, hide_terrain_zones=hide_terrain_zones):
            skipped += 1
            continue
        markers.append(marker)
    if invalid:
        problems.append(f'{invalid} marqueur(s) ignoré(s) : position[] invalide ou absente.')
    if skipped:
        problems.append(f'{skipped} marqueur(s) technique(s) ignoré(s).')
    if total_blocks == 0:
        problems.append('Aucun marqueur Eden (dataType="Marker") dans mission.sqm.')
    return markers, problems


def extract_markers_from_pbo(pbo) -> tuple[list[dict], list[str]]:
    """Extrait les marqueurs depuis mission.sqm dans le PBO."""
    binarized = is_sqm_binarized(pbo)
    if binarized is True:
        return [], ['mission.sqm binarisé : marqueurs non extraits']
    if binarized is None:
        return [], []
    try:
        sqm_content = pbo['mission.sqm'].data.decode('utf-8', errors='replace')
    except Exception as e:
        return [], [f'Erreur lors de la lecture de mission.sqm : {e}']
    markers, problems = extract_markers_from_sqm(sqm_content)
    script_configs = extract_script_marker_configs_from_pbo(pbo)
    if script_configs:
        markers, config_count = apply_script_marker_configs(markers, script_configs)
        if config_count:
            problems.append(
                f'{config_count} marqueur(s) enrichi(s) depuis scripts SQF (forme/taille).'
            )
    marker_names = [(m.get('name') or '').strip() for m in markers]
    script_hidden = extract_script_hidden_markers_from_pbo(pbo, marker_names=marker_names)
    if script_hidden:
        markers, hidden_count = filter_markers_hidden_by_script(markers, script_hidden)
        if hidden_count:
            sample = ', '.join(sorted(script_hidden)[:5])
            suffix = '…' if len(script_hidden) > 5 else ''
            problems.append(
                f'{hidden_count} marqueur(s) masqué(s) par script SQF au démarrage '
                f'({sample}{suffix}).'
            )
    return markers, problems


def markers_abs_path(rel_path: str) -> Path:
    """Chemin absolu d'un JSON marqueurs sous MEDIA_ROOT."""
    from django.conf import settings

    clean = (rel_path or '').replace('\\', '/').lstrip('/')
    return Path(settings.MEDIA_ROOT) / clean


def save_markers_to_storage(markers: list, *, mission_id: int | None = None) -> str | None:
    """Écrit la liste de marqueurs en JSON sur disque ; retourne le chemin relatif ou None."""
    if not markers:
        return None
    from django.conf import settings

    filename = f'{mission_id}.json' if mission_id is not None else f'{uuid.uuid4()}.json'
    rel_path = os.path.join(settings.MISSIONS_MARKERS_STORAGE_PATH, filename).replace('\\', '/')
    abs_path = markers_abs_path(rel_path)
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(markers, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    abs_path.write_bytes(payload)
    if not abs_path.is_file() or abs_path.stat().st_size == 0:
        raise OSError(f'Ecriture marqueurs echouee: {abs_path}')
    return rel_path


def delete_markers_file(path: str | None) -> None:
    """Supprime le fichier JSON de marqueurs (ignore si absent)."""
    if not path:
        return
    try:
        markers_abs_path(path).unlink(missing_ok=True)
    except Exception:
        pass


def _markers_file_search_paths(rel_path: str) -> list:
    """Chemins absolus possibles pour un JSON marqueurs (volume actuel + legacy)."""
    from django.conf import settings

    name = Path(rel_path).name
    rel_candidates = [
        rel_path,
        Path(settings.MISSIONS_MARKERS_STORAGE_PATH, name).as_posix(),
        Path('markers', name).as_posix(),
    ]
    seen: set[str] = set()
    paths: list[Path] = []
    for rel in rel_candidates:
        if rel in seen:
            continue
        seen.add(rel)
        paths.append(markers_abs_path(rel))
    for base in (
        Path(settings.MEDIA_ROOT),
        Path(settings.BASE_DIR) / 'missions',
        Path('/app/missions'),
    ):
        for rel in rel_candidates:
            paths.append(base / rel)
    unique: list[Path] = []
    seen_abs: set[str] = set()
    for path in paths:
        key = str(path)
        if key in seen_abs:
            continue
        seen_abs.add(key)
        unique.append(path)
    return unique


def _read_markers_json_file(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, list):
        raise ValueError(f'Format JSON invalide: {path}')
    return [m for m in data if isinstance(m, dict)]


def _migrate_markers_file_to_storage(mission, source: Path) -> str | None:
    """Copie un JSON legacy vers le stockage media canonique et met a jour la mission."""
    try:
        markers = _read_markers_json_file(source)
    except Exception as exc:
        logging.warning('Migration marqueurs impossible depuis %s: %s', source, exc)
        return None
    if not markers:
        return None
    rel_path = save_markers_to_storage(markers, mission_id=mission.id)
    if not rel_path:
        return None
    mission.markers_file = rel_path
    mission.save(update_fields=['markers_file'])
    logging.info(
        'Marqueurs migres mission id=%s: %s -> %s',
        mission.id,
        source,
        rel_path,
    )
    return rel_path


def load_markers_raw_from_mission(mission, *, migrate_legacy: bool = True) -> tuple[list[dict], str | None]:
    """Lit le JSON brut de marqueurs ; retourne (liste, erreur eventuelle)."""
    path = getattr(mission, 'markers_file', '') or ''
    if not path:
        return [], None

    abs_path = markers_abs_path(path)
    if abs_path.is_file():
        try:
            return _read_markers_json_file(abs_path), None
        except Exception as exc:
            logging.warning('Impossible de lire les marqueurs %s: %s', abs_path, exc)
            return [], str(exc)

    if migrate_legacy:
        for candidate in _markers_file_search_paths(path):
            if candidate.is_file() and candidate != abs_path:
                migrated = _migrate_markers_file_to_storage(mission, candidate)
                if migrated:
                    return load_markers_raw_from_mission(mission, migrate_legacy=False)

    return [], f'Fichier introuvable: {path}'


def get_mission_markers_status(mission) -> dict:
    """Resume l'etat marqueurs pour l'UI (diagnostic)."""
    path = getattr(mission, 'markers_file', '') or ''
    raw, error = load_markers_raw_from_mission(mission)
    visible = [m for m in raw if is_marker_visible(m)]
    hidden = len(raw) - len(visible)
    return {
        'path': path,
        'raw_count': len(raw),
        'visible_count': len(visible),
        'hidden_count': hidden,
        'error': error,
        'visible': visible,
        'raw': raw,
    }


def load_markers_from_mission(mission) -> list[dict]:
    """Lit les marqueurs affichables depuis markers_file."""
    return get_mission_markers_status(mission)['visible']
