import logging
import re

# Briefing Arma : balises utiles au rendu, sans event handlers / scripts.
BRIEFING_ALLOWED_TAGS = frozenset({
    'br', 'b', 'u', 'i', 'strong', 'em', 'p', 'h1', 'h2', 'h3', 'h4',
    'a', 'img', 'font', 'span', 'div', 'ul', 'ol', 'li',
})
BRIEFING_ALLOWED_ATTRIBUTES = {
    'a': ['href', 'title', 'name'],
    'img': ['src', 'alt', 'width', 'height'],
    'font': ['color', 'size', 'face'],
}
BRIEFING_ALLOWED_PROTOCOLS = frozenset({'http', 'https', 'mailto'})


def sanitize_briefing_html(html):
    """Nettoie le HTML de briefing (XSS) en conservant le formatage Arma usuel."""
    if not html:
        return html or ''
    import bleach

    return bleach.clean(
        html,
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
    Remplace les balises <marker ...> par <u><b>...</b></u>.
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
                    # Remplacement des balises marker par leur contenu souligné
                    item_content = _re.sub(r"<\s*marker[^>]*>(.*?)<\s*/\s*marker\s*>", r'<u><b>\1</b></u>', item_content, flags=_re.DOTALL)
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
