"""Mapping des cartes GDC vers Arma3Map (Leaflet)."""

from __future__ import annotations

import json
from pathlib import Path

# plan-ops renvoie souvent 403 ; GitHub Pages sert de CDN principal.
CDN_PRIMARY = 'https://jetelain.github.io/Arma3Map'
CDN_FALLBACK = 'https://mapsdata.plan-ops.fr'
MARKERS_ICON_CDN = (
    'https://cdn.jsdelivr.net/gh/jetelain/Arma3TacMap@main/'
    'Arma3TacMapLibrary/wwwroot/img/markers'
)

# Icônes A3 : pack OCAP2 (https://github.com/OCAP2/web — assets/markers/a3).
_MARKERS_DIR = Path(__file__).resolve().parent / 'static' / 'gdc_storm' / 'markers'
_MARKER_MANIFEST_PATH = _MARKERS_DIR / 'manifest.json'


def _load_marker_icon_map() -> dict[str, str]:
    """lowercase stem -> filename réel (casse Linux)."""
    if _MARKER_MANIFEST_PATH.is_file():
        data = json.loads(_MARKER_MANIFEST_PATH.read_text(encoding='utf-8'))
        files = data.get('files') or {}
        if isinstance(files, dict) and files:
            return {str(k).lower(): str(v) for k, v in files.items()}
    # Fallback scan disque si manifest absent.
    mapping: dict[str, str] = {}
    if _MARKERS_DIR.is_dir():
        for path in _MARKERS_DIR.glob('*.png'):
            mapping[path.stem.lower()] = path.name
    return mapping


MARKER_ICON_MAP: dict[str, str] = _load_marker_icon_map()
MARKER_ICON_FILES: frozenset[str] = frozenset(MARKER_ICON_MAP.values())
# Cles JS Arma3Map (repo jetelain/Arma3Map, dossier maps/*.js) — 133 cartes.
ARMA3MAP_AVAILABLE: frozenset[str] = frozenset({
    'Chernarus_Winter', 'abramia', 'albasrah', 'all', 'altis', 'archipelago',
    'australia', 'bala_murghab_summer', 'bala_murghab_winter', 'beketov',
    'belavezhsk', 'blud_vidda', 'bornholm', 'bozcaada', 'brf_sumava', 'btrx_sngt',
    'cam_lao_nam', 'cartercity', 'cfb_moosehead', 'chernarus', 'chernarus_a3s',
    'chernarus_summer', 'chongo', 'clafghan', 'ctm_front', 'cup_chernarus_a3',
    'dagger_island_summer', 'deniland', 'dingor', 'dya', 'eaw_jungleskirmish',
    'eaw_map', 'eaw_mountainpasses', 'eaw_village_1', 'edaly_map_alpha', 'eden',
    'egl_laghisola', 'enoch', 'esseker', 'fallujah', 'fapovo', 'farabad',
    'farkhar', 'fata', 'fow_map_gold', 'gm_weferlingen', 'gm_weferlingen_summer',
    'gm_weferlingen_winter', 'green_sea', 'grozny', 'gulfcoast', 'hebontes',
    'hellanmaa', 'hellanmaaw', 'hindukush', 'isladuala3', 'jns_tria',
    'juju_kalahari', 'juju_sahatra', 'k9s_bastek', 'kapaulio', 'kerama',
    'khoramshahr', 'kujari', 'kunduz', 'kunduz_valley', 'lingor3', 'lythium',
    'malden', 'mbg_celle2', 'mcn_aliabad', 'mcn_hazarkot', 'mountains_acr',
    'napf', 'napfwinter', 'northtakistan', 'optre_phobos', 'oski_corran',
    'pabst_yellowstone', 'panthera3', 'pja305', 'pja306', 'pja307', 'pja310',
    'pja314', 'pja319', 'porto', 'pulau', 'regero', 'reshmaan', 'rhspkl',
    'rksla3_t_bov_test_area', 'rof_mok', 'ruha', 'rut_mandol',
    'sangin_distirict_helmand_province', 'sara', 'sara_dbe1', 'saralite',
    'seangola', 'sefrouramal', 'sehreno', 'senkakuislands_2035', 'shapur_baf',
    'stratis', 'swu_public_rhode_map', 'swu_public_salman_map', 'sze_kimmirut',
    'takistan', 'tanoa', 'taunus', 'tem_anizay', 'tem_lang_dan', 'tem_suursaariv',
    'tembelan', 'todt', 'tps_diegogarcia', 'ukaf_batus', 'umb_armavir',
    'umb_colombia', 'uzbin', 'vn_khe_sanh', 'vn_the_bra', 'vr', 'vt5', 'vt7',
    'vtf_korsac', 'vtf_korsac_winter', 'vtf_lybor', 'wake', 'wl_rosche',
    'woodland_acr', 'zargabad',
})

# Alias GDC / PBO -> cle Arma3Map (en plus des identites ci-dessous).
_MAP_ALIASES: dict[str, str] = {
    # Chernarus / CUP
    'chernarus_a3': 'chernarus',
    'cup_chernarus': 'chernarus',
    'chernarus_winter': 'Chernarus_Winter',
    # Zargabad
    'cup_zargabad_a3': 'zargabad',
    # Livonia
    'livonia': 'enoch',
    # Orthographes / anciens codes GDC
    'isladual3': 'isladuala3',
    'isladuala': 'isladuala3',
    'lingor': 'lingor3',
    'rosche': 'wl_rosche',
    'wl_roche': 'wl_rosche',
    'tem_kujari': 'kujari',
    'utes': 'porto',  # Utes non dispo ; Porto proche (petite ile)
    'bootcamp_acr': 'mountains_acr',
    'prei_khmaoch_luong': 'cam_lao_nam',  # Indochine (prei_kma absent du CDN)
    'prei_kma': 'cam_lao_nam',
}

# Identite pour chaque carte CDN + alias (cles en minuscules).
MAP_CODE_TO_ARMA3MAP: dict[str, str] = {
    **{k.lower(): k for k in ARMA3MAP_AVAILABLE},
    **{k.lower(): v for k, v in _MAP_ALIASES.items()},
}


# Couleurs Eden Arma 3 (colorName → CSS). Valeurs proches du rendu in-game.
ARMA_MARKER_COLORS: dict[str, str] = {
    # Eden : sans couleur explicite, les icones sont noires.
    'Default': '#000000',
    'ColorBlack': '#000000',
    'ColorGrey': '#808080',
    'ColorGray': '#808080',
    'ColorRed': '#FF0000',
    'ColorBrown': '#804000',
    'ColorOrange': '#FF8000',
    'ColorYellow': '#FFFF00',
    'ColorGreen': '#00C000',
    'ColorBlue': '#0000FF',
    'ColorPink': '#FF00FF',
    'ColorPurple': '#800080',
    'ColorWhite': '#FFFFFF',
    'ColorWEST': '#0066CC',
    'ColorEAST': '#CC0000',
    'ColorGUER': '#00CC00',
    'ColorCIV': '#FFFF00',
}


def resolve_marker_color_css(color: str) -> str | None:
    """Retourne une couleur CSS (#hex) pour un colorName Eden ou une valeur #hex deja stockee."""
    raw = (color or '').strip()
    if not raw:
        return None
    if raw.startswith('#'):
        return raw
    if raw in ARMA_MARKER_COLORS:
        return ARMA_MARKER_COLORS[raw]
    lower = raw.lower()
    for key, value in ARMA_MARKER_COLORS.items():
        if key.lower() == lower:
            return value
    if not raw.startswith('Color') and not raw.startswith('color'):
        tail = raw[0].upper() + raw[1:] if raw else ''
        prefixed = f'Color{tail}'
        if prefixed in ARMA_MARKER_COLORS:
            return ARMA_MARKER_COLORS[prefixed]
    return None


def marker_type_is_flag(marker_type: str) -> bool:
    raw = (marker_type or '').strip().lower()
    if raw.endswith('_noshadow'):
        raw = raw[: -len('_noshadow')]
    return raw.startswith('flag_')


def is_zone_marker_type(marker_type: str) -> bool:
    raw = (marker_type or '').strip().lower()
    if raw.endswith('_noshadow'):
        raw = raw[: -len('_noshadow')]
    return raw in ('rectangle', 'ellipse')


def resolve_marker_icon_filename(marker_type: str) -> str:
    """Retourne le nom de fichier PNG pour un type de marqueur Arma 3 (pack OCAP2)."""
    raw = (marker_type or '').strip().lower()
    if raw.endswith('_noshadow'):
        raw = raw[: -len('_noshadow')]
    if raw in ('rectangle', 'ellipse', 'empty', 'icon'):
        return MARKER_ICON_MAP.get('hd_dot', 'hd_dot.png')
    if raw in MARKER_ICON_MAP:
        return MARKER_ICON_MAP[raw]
    if raw.startswith('hd_') or raw.startswith('mil_'):
        return MARKER_ICON_MAP.get('hd_unknown', 'hd_unknown.png')
    if raw.startswith('flag_'):
        return MARKER_ICON_MAP.get(raw) or MARKER_ICON_MAP.get('hd_flag', 'hd_flag.png')
    # NATO / loc inconnus : fallback générique.
    if len(raw) >= 3 and raw[1] == '_':
        return MARKER_ICON_MAP.get('mil_marker', 'mil_marker.png')
    if raw.startswith('loc_'):
        return MARKER_ICON_MAP.get('mil_marker', 'mil_marker.png')
    return MARKER_ICON_MAP.get('hd_unknown', 'hd_unknown.png')


def get_arma3map_config(map_code: str) -> dict | None:
    """Retourne la config Arma3Map pour un code carte GDC, ou None si non supportée."""
    key = MAP_CODE_TO_ARMA3MAP.get((map_code or '').strip().lower())
    if not key:
        return None
    return {
        'map_key': key,
        'cdn_base': CDN_PRIMARY,
        'cdn_fallback': CDN_FALLBACK,
        'map_js_url': f'{CDN_PRIMARY}/maps/{key}.js',
        'utils_js_url': f'{CDN_PRIMARY}/js/mapUtils.js',
        'css_url': f'{CDN_PRIMARY}/css/mapUtils.css',
        'markers_icon_base': MARKERS_ICON_CDN,
    }


def with_local_map_assets(config: dict | None) -> dict | None:
    """Sert Leaflet, mapUtils et icones marqueurs depuis /static/ (first-party)."""
    if not config:
        return None
    from django.templatetags.static import static

    return {
        **config,
        'leaflet_css_url': static('gdc_storm/vendor/leaflet/leaflet.css'),
        'leaflet_js_url': static('gdc_storm/vendor/leaflet/leaflet.js'),
        'utils_js_url': static('gdc_storm/vendor/arma3map/mapUtils.js'),
        'css_url': static('gdc_storm/vendor/arma3map/mapUtils.css'),
        'markers_icon_base': static('gdc_storm/markers').rstrip('/'),
    }
