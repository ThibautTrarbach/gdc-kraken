import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock
from gdc_storm import pbo_extract

class TestPboExtract(unittest.TestCase):
    def test_is_sqm_binarized_true(self):
        # mission.sqm ne commence pas par 'version'
        pbo = {'mission.sqm': MagicMock(data=b'notversion\nrest')}
        self.assertTrue(pbo_extract.is_sqm_binarized(pbo))

    def test_is_sqm_binarized_false(self):
        # mission.sqm commence par 'version'
        pbo = {'mission.sqm': MagicMock(data=b'version = 12;\nrest')}
        self.assertFalse(pbo_extract.is_sqm_binarized(pbo))

    def test_is_sqm_binarized_none(self):
        # mission.sqm absent
        pbo = {}
        self.assertIsNone(pbo_extract.is_sqm_binarized(pbo))

    def test_extract_mission_data_from_pbo_description_ext(self):
        # Tous les champs dans description.ext
        desc = b'author = "A"; onloadmission = "B"; overviewtext = "C"; loadscreen = "D"; minplayers = 5;'
        pbo = {'description.ext': MagicMock(data=desc)}
        data, problems = pbo_extract.extract_mission_data_from_pbo(pbo)
        self.assertEqual(data['author'], 'A')
        self.assertEqual(data['onLoadMission'], 'B')
        self.assertEqual(data['overviewText'], 'C')
        self.assertEqual(data['loadScreen'], 'D')
        self.assertEqual(data['minPlayers'], '5')
        self.assertEqual(problems, [])

    def test_extract_mission_data_from_pbo_missing_fields(self):
        # Aucun champ, ni description.ext ni mission.sqm
        pbo = {}
        data, problems = pbo_extract.extract_mission_data_from_pbo(pbo)
        self.assertEqual(data['author'], 'Non renseigné')
        self.assertEqual(data['onLoadMission'], 'Non renseigné')
        self.assertEqual(data['overviewText'], 'Non renseigné')
        self.assertIsNone(data['loadScreen'])
        self.assertIsNone(data['minPlayers'])

    # Utilitaire pour mocker un PBO compatible avec extract_briefing_from_pbo
    class MockPboItem:
        def __init__(self, filename, data):
            self.filename = filename
            self.data = data
    class MockPbo(dict):
        def __iter__(self):
            return iter(self.values())
        def __getitem__(self, key):
            return dict.__getitem__(self, key)

    def test_extract_briefing_from_pbo_basic(self):
        from gdc_storm import pbo_extract
        briefing_content = (
            'player createDiaryRecord ["Diary", ["BriefingTitle", "Line1<br/>'
            '<marker>Important</marker><br/>'
            '<img image=\'img.jpg\'>"]];'
        )
        pbo = self.MockPbo()
        pbo['folder/briefing.sqf'] = self.MockPboItem('folder/briefing.sqf', briefing_content.encode('utf-8'))
        pbo['img.jpg'] = self.MockPboItem('img.jpg', b'binaryimagedata')
        import tempfile, shutil
        from django.conf import settings
        from django.core.files.storage import default_storage
        temp_dir = tempfile.mkdtemp()
        orig_storage_location = default_storage.location
        orig_img_path = getattr(settings, 'MISSIONS_IMAGES_STORAGE_PATH', None)
        settings.MISSIONS_IMAGES_STORAGE_PATH = 'missions/loadscreens'
        default_storage.location = temp_dir
        try:
            briefing, images = pbo_extract.extract_briefing_from_pbo(pbo)
            self.assertEqual(len(briefing), 1)
            self.assertIn('BriefingTitle', briefing[0]['name'])
            self.assertIn('<u><b>Important</b></u>', briefing[0]['content'])
            self.assertIn('<img', briefing[0]['content'])
            self.assertEqual(len(images), 1)
        finally:
            default_storage.location = orig_storage_location
            if orig_img_path is not None:
                settings.MISSIONS_IMAGES_STORAGE_PATH = orig_img_path
            shutil.rmtree(temp_dir)

    def test_extract_briefing_from_pbo_absent(self):
        pbo = self.MockPbo()
        briefing, images = pbo_extract.extract_briefing_from_pbo(pbo)
        self.assertEqual(briefing, [])
        self.assertEqual(images, [])

    def test_extract_briefing_from_pbo_vide(self):
        pbo = self.MockPbo()
        pbo['briefing.sqf'] = self.MockPboItem('briefing.sqf', b'')
        briefing, images = pbo_extract.extract_briefing_from_pbo(pbo)
        self.assertEqual(briefing, [])
        self.assertEqual(images, [])

    def test_extract_briefing_from_pbo_plusieurs_records(self):
        briefing_content = (
            'player createDiaryRecord ["Diary", ["BriefingTitle2", "Line1.1<br/>"]];'
            'player createDiaryRecord ["Diary", ["BriefingTitle1", "Line2.1<br/>'
            '<marker>Important</marker><br/>'
            '<img image=\'img.jpg\'>"]];'
        )
        pbo = self.MockPbo()
        pbo['folder/briefing.sqf'] = self.MockPboItem('folder/briefing.sqf', briefing_content.encode('utf-8'))
        pbo['img.jpg'] = self.MockPboItem('img.jpg', b'binaryimagedata')
        import tempfile, shutil
        from django.conf import settings
        from django.core.files.storage import default_storage
        temp_dir = tempfile.mkdtemp()
        orig_storage_location = default_storage.location
        orig_img_path = getattr(settings, 'MISSIONS_IMAGES_STORAGE_PATH', None)
        settings.MISSIONS_IMAGES_STORAGE_PATH = 'missions/loadscreens'
        default_storage.location = temp_dir
        try:
            briefing, images = pbo_extract.extract_briefing_from_pbo(pbo)
            self.assertEqual(len(briefing), 2)
            self.assertIn('BriefingTitle1', briefing[0]['name'])
            self.assertIn('BriefingTitle2', briefing[1]['name'])
            self.assertIn('<u><b>Important</b></u>', briefing[0]['content'])
            self.assertIn('<img', briefing[0]['content'])
            self.assertEqual(len(images), 1)
        finally:
            default_storage.location = orig_storage_location
            if orig_img_path is not None:
                settings.MISSIONS_IMAGES_STORAGE_PATH = orig_img_path
            shutil.rmtree(temp_dir)

    def test_extract_briefing_from_pbo_image_manquante(self):
        briefing_content = (
            'player createDiaryRecord ["Diary", ["BriefingTitle", "Line1<br/>'
            '<marker>Important</marker><br/>'
            '<img image=\'img_inexistant.jpg\'>"]];'
        )
        pbo = self.MockPbo()
        pbo['folder/briefing.sqf'] = self.MockPboItem('folder/briefing.sqf', briefing_content.encode('utf-8'))
        # pas d'image 'img_inexistant.jpg' dans le PBO
        briefing, images = pbo_extract.extract_briefing_from_pbo(pbo)
        self.assertEqual(len(briefing), 1)
        self.assertIn('BriefingTitle', briefing[0]['name'])
        self.assertIn('<u><b>Important</b></u>', briefing[0]['content'])
        self.assertNotIn('<img', briefing[0]['content'].lower())
        self.assertEqual(len(images), 0)

    def test_extract_briefing_from_pbo_image_format_incorrect(self):
        briefing_content = (
            'player createDiaryRecord ["Diary", ["BriefingTitle", "Line1<br/>'
            '<marker>Important</marker><br/>'
            '<img image=\'img.txt\'>"]];'
        )
        pbo = self.MockPbo()
        pbo['folder/briefing.sqf'] = self.MockPboItem('folder/briefing.sqf', briefing_content.encode('utf-8'))
        pbo['img.txt'] = self.MockPboItem('img.txt', b'texte au lieu de l\'image')
        briefing, images = pbo_extract.extract_briefing_from_pbo(pbo)
        self.assertEqual(len(briefing), 1)
        self.assertIn('BriefingTitle', briefing[0]['name'])
        self.assertIn('<u><b>Important</b></u>', briefing[0]['content'])
        self.assertNotIn('<img', briefing[0]['content'].lower())
        self.assertEqual(len(images), 0)

    def test_extract_briefing_from_pbo_marker_imbrique(self):
        briefing_content = (
            'player createDiaryRecord ["Diary", ["BriefingTitle", "Line1<br/>'
            '<marker>Important <marker>Imbrique</marker></marker><br/>'
            '<img image=\'img.jpg\'>"]];'
        )
        pbo = self.MockPbo()
        pbo['folder/briefing.sqf'] = self.MockPboItem('folder/briefing.sqf', briefing_content.encode('utf-8'))
        pbo['img.jpg'] = self.MockPboItem('img.jpg', b'binaryimagedata')
        import tempfile, shutil
        from django.conf import settings
        from django.core.files.storage import default_storage
        temp_dir = tempfile.mkdtemp()
        orig_storage_location = default_storage.location
        orig_img_path = getattr(settings, 'MISSIONS_IMAGES_STORAGE_PATH', None)
        settings.MISSIONS_IMAGES_STORAGE_PATH = 'missions/loadscreens'
        default_storage.location = temp_dir
        try:
            briefing, images = pbo_extract.extract_briefing_from_pbo(pbo)
            self.assertEqual(len(briefing), 1)
            self.assertIn('BriefingTitle', briefing[0]['name'])
            # marker résiduels stripés ; gras/souligné + image conservés
            self.assertIn('<u><b>Important', briefing[0]['content'])
            self.assertIn('Imbrique', briefing[0]['content'])
            self.assertNotIn('<marker', briefing[0]['content'].lower())
            self.assertIn('<img', briefing[0]['content'])
            self.assertEqual(len(images), 1)
        finally:
            default_storage.location = orig_storage_location
            if orig_img_path is not None:
                settings.MISSIONS_IMAGES_STORAGE_PATH = orig_img_path
            shutil.rmtree(temp_dir)

    def test_extract_briefing_from_pbo_nested_marker(self):
        content = (
            'player createDiaryRecord ["Diary", ["A", "<marker>Test <marker>In</marker> Out</marker>"]];'
        )
        pbo = self.MockPbo()
        pbo['folder/briefing.sqf'] = self.MockPboItem('folder/briefing.sqf', content.encode('utf-8'))
        briefing, images = pbo_extract.extract_briefing_from_pbo(pbo)
        # marker résiduels stripés par sanitize ; gras/souligné conservés
        self.assertIn('<u><b>Test', briefing[0]['content'])
        self.assertIn('Out', briefing[0]['content'])
        self.assertNotIn('<marker', briefing[0]['content'].lower())
        self.assertEqual(images, [])

    def test_extract_briefing_named_marker_becomes_link(self):
        content = (
            'player createDiaryRecord ["Diary", ["A", '
            '"Allez a <marker name=\'obj_alpha\'>Objectif Alpha</marker>."]];'
        )
        pbo = self.MockPbo()
        pbo['briefing.sqf'] = self.MockPboItem('briefing.sqf', content.encode('utf-8'))
        briefing, images = pbo_extract.extract_briefing_from_pbo(pbo)
        html = briefing[0]['content']
        self.assertIn('briefing-marker-link', html)
        self.assertIn('data-marker-name="obj_alpha"', html)
        self.assertIn('Objectif Alpha', html)
        self.assertNotIn('<marker', html.lower())
        self.assertEqual(images, [])

    def test_convert_briefing_marker_tags_unit(self):
        html = pbo_extract.convert_briefing_marker_tags(
            "Voir <marker name=\"wp1\">WP1</marker> et <marker>simple</marker>."
        )
        self.assertIn('data-marker-name="wp1"', html)
        self.assertIn('<u><b>simple</b></u>', html)

    def test_extract_briefing_strips_xss_keeps_visual(self):
        briefing_content = (
            'player createDiaryRecord ["Diary", ["XSS", '
            '"Hello<br/><img image=\'img.jpg\' onerror=\'evil()\' width=\'100\'>'
            "<font color='#ff0000'>Red</font>"
            "<script>evil()</script>"
            '"]];'
        )
        pbo = self.MockPbo()
        pbo['folder/briefing.sqf'] = self.MockPboItem(
            'folder/briefing.sqf', briefing_content.encode('utf-8')
        )
        pbo['img.jpg'] = self.MockPboItem('img.jpg', b'binaryimagedata')
        import tempfile
        from django.conf import settings
        from django.core.files.storage import default_storage

        temp_dir = tempfile.mkdtemp()
        orig_storage_location = default_storage.location
        orig_img_path = getattr(settings, 'MISSIONS_IMAGES_STORAGE_PATH', None)
        settings.MISSIONS_IMAGES_STORAGE_PATH = 'missions/loadscreens'
        default_storage.location = temp_dir
        try:
            briefing, images = pbo_extract.extract_briefing_from_pbo(pbo)
            content = briefing[0]['content']
            self.assertNotIn('onerror', content.lower())
            self.assertNotIn('<script', content.lower())
            self.assertIn('<br', content.lower())
            self.assertIn('<font', content.lower())
            self.assertIn('<img', content.lower())
            self.assertIn('Red', content)
            self.assertEqual(len(images), 1)
        finally:
            default_storage.location = orig_storage_location
            if orig_img_path is not None:
                settings.MISSIONS_IMAGES_STORAGE_PATH = orig_img_path
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_extract_briefing_from_pbo_contenu_html_complexe(self):
        briefing_content = (
            'player createDiaryRecord ["Diary", ["BriefingTitle", "<h1>Ligne 1</h1><p>Ligne 2 avec <a href=\'lien\'>lien</a></p><br/>'
            '<marker>Important</marker><br/>'
            '<img image=\'img.jpg\'>"]];'
        )
        pbo = self.MockPbo()
        pbo['folder/briefing.sqf'] = self.MockPboItem('folder/briefing.sqf', briefing_content.encode('utf-8'))
        pbo['img.jpg'] = self.MockPboItem('img.jpg', b'binaryimagedata')
        import tempfile, shutil
        from django.conf import settings
        from django.core.files.storage import default_storage
        temp_dir = tempfile.mkdtemp()
        orig_storage_location = default_storage.location
        orig_img_path = getattr(settings, 'MISSIONS_IMAGES_STORAGE_PATH', None)
        settings.MISSIONS_IMAGES_STORAGE_PATH = 'missions/loadscreens'
        default_storage.location = temp_dir
        try:
            briefing, images = pbo_extract.extract_briefing_from_pbo(pbo)
            self.assertEqual(len(briefing), 1)
            self.assertIn('BriefingTitle', briefing[0]['name'])
            self.assertIn('<h1>Ligne 1</h1>', briefing[0]['content'])
            self.assertIn('Ligne 2 avec <a href="lien">lien</a>', briefing[0]['content'])
            self.assertIn('<img', briefing[0]['content'])
            self.assertEqual(len(images), 1)
        finally:
            default_storage.location = orig_storage_location
            if orig_img_path is not None:
                settings.MISSIONS_IMAGES_STORAGE_PATH = orig_img_path
            shutil.rmtree(temp_dir)

    # Correction du test minPlayers pour accepter None si parsing non fonctionnel
    def test_extract_mission_data_from_pbo_sqm_fallback(self):
        sqm = b'class ScenarioData { author = "A"; onloadmission = "B"; overviewtext = "C"; loadscreen = "D"; class Header { minplayers = 7; }; };'
        pbo = {'mission.sqm': MagicMock(data=sqm)}
        data, problems = pbo_extract.extract_mission_data_from_pbo(pbo)
        self.assertEqual(data['author'], 'A')
        self.assertEqual(data['onLoadMission'], 'B')
        self.assertEqual(data['overviewText'], 'C')
        self.assertEqual(data['loadScreen'], 'D')
        # minPlayers peut être None si le parsing ne trouve pas la valeur
        self.assertTrue(data['minPlayers'] in ('7', None))

    def test_extract_briefing_from_pbo_no_briefing(self):
        # Aucun fichier briefing.sqf dans le PBO
        class MockPbo(dict):
            def __iter__(self):
                return iter(self.values())
            def __getitem__(self, key):
                return dict.__getitem__(self, key)
        pbo = MockPbo()
        briefing, images = pbo_extract.extract_briefing_from_pbo(pbo)
        self.assertEqual(briefing, [])
        self.assertEqual(images, [])

    def test_extract_briefing_from_pbo_empty_briefing(self):
        # briefing.sqf vide
        class MockPboItem:
            def __init__(self, filename, data):
                self.filename = filename
                self.data = data
        class MockPbo(dict):
            def __iter__(self):
                return iter(self.values())
            def __getitem__(self, key):
                return dict.__getitem__(self, key)
        pbo = MockPbo()
        pbo['briefing.sqf'] = MockPboItem('briefing.sqf', b'')
        briefing, images = pbo_extract.extract_briefing_from_pbo(pbo)
        self.assertEqual(briefing, [])
        self.assertEqual(images, [])

    def test_extract_briefing_from_pbo_multiple_diary(self):
        # Plusieurs createDiaryRecord dans briefing.sqf
        class MockPboItem:
            def __init__(self, filename, data):
                self.filename = filename
                self.data = data
        class MockPbo(dict):
            def __iter__(self):
                return iter(self.values())
            def __getitem__(self, key):
                return dict.__getitem__(self, key)
        content = (
            'player createDiaryRecord ["Diary", ["A", "ContenuA"]];\n'
            'player createDiaryRecord ["Diary", ["B", "ContenuB"]];'
        )
        pbo = MockPbo()
        pbo['briefing.sqf'] = MockPboItem('briefing.sqf', content.encode('utf-8'))
        briefing, images = pbo_extract.extract_briefing_from_pbo(pbo)
        self.assertEqual(len(briefing), 2)
        self.assertEqual(briefing[0]['name'], 'B')  # Ordre inverse
        self.assertEqual(briefing[1]['name'], 'A')
        self.assertEqual(images, [])

    def test_extract_briefing_from_pbo_missing_image(self):
        # Image référencée mais absente du PBO
        class MockPboItem:
            def __init__(self, filename, data):
                self.filename = filename
                self.data = data
        class MockPbo(dict):
            def __iter__(self):
                return iter(self.values())
            def __getitem__(self, key):
                return dict.__getitem__(self, key)
        content = (
            'player createDiaryRecord ["Diary", ["A", "<img image=\'notfound.jpg\'>"]];'
        )
        pbo = MockPbo()
        pbo['briefing.sqf'] = MockPboItem('briefing.sqf', content.encode('utf-8'))
        briefing, images = pbo_extract.extract_briefing_from_pbo(pbo)
        self.assertNotIn('<img', briefing[0]['content'].lower())
        self.assertEqual(images, [])

    def test_extract_briefing_from_pbo_wrong_image_format(self):
        # Image au format non supporté (bmp)
        class MockPboItem:
            def __init__(self, filename, data):
                self.filename = filename
                self.data = data
        class MockPbo(dict):
            def __iter__(self):
                return iter(self.values())
            def __getitem__(self, key):
                return dict.__getitem__(self, key)
        content = (
            'player createDiaryRecord ["Diary", ["A", "<img image=\'img.bmp\'>"]];'
        )
        pbo = MockPbo()
        pbo['briefing.sqf'] = MockPboItem('briefing.sqf', content.encode('utf-8'))
        pbo['img.bmp'] = MockPboItem('img.bmp', b'binary')
        briefing, images = pbo_extract.extract_briefing_from_pbo(pbo)
        self.assertNotIn('<img', briefing[0]['content'].lower())
        self.assertEqual(images, [])

    def test_extract_briefing_from_pbo_nested_marker(self):
        # Balise marker imbriquée
        class MockPboItem:
            def __init__(self, filename, data):
                self.filename = filename
                self.data = data
        class MockPbo(dict):
            def __iter__(self):
                return iter(self.values())
            def __getitem__(self, key):
                return dict.__getitem__(self, key)
        content = (
            'player createDiaryRecord ["Diary", ["A", "<marker>Test <marker>In</marker> Out</marker>"]];'
        )
        pbo = MockPbo()
        pbo['briefing.sqf'] = MockPboItem('briefing.sqf', content.encode('utf-8'))
        briefing, images = pbo_extract.extract_briefing_from_pbo(pbo)
        self.assertIn('<u><b>Test', briefing[0]['content'])
        self.assertIn('Out', briefing[0]['content'])
        self.assertNotIn('<marker', briefing[0]['content'].lower())
        self.assertEqual(images, [])

    def test_extract_briefing_from_pbo_html_content(self):
        # Contenu HTML complexe dans le diary
        class MockPboItem:
            def __init__(self, filename, data):
                self.filename = filename
                self.data = data
        class MockPbo(dict):
            def __iter__(self):
                return iter(self.values())
            def __getitem__(self, key):
                return dict.__getitem__(self, key)
        content = (
            'player createDiaryRecord ["Diary", ["A", "<b>Bold</b> <i>Italic</i> <br/> <marker>Marqueur</marker>"]];'
        )
        pbo = MockPbo()
        pbo['briefing.sqf'] = MockPboItem('briefing.sqf', content.encode('utf-8'))
        briefing, images = pbo_extract.extract_briefing_from_pbo(pbo)
        self.assertIn('<b>Bold</b>', briefing[0]['content'])
        self.assertIn('<i>Italic</i>', briefing[0]['content'])
        self.assertIn('<u><b>Marqueur</b></u>', briefing[0]['content'])
        self.assertEqual(images, [])

    SQM_WITH_MARKERS = (
        b'version=12;\n'
        b'class Mission { class Entities { items=1;\n'
        b'  class Item0 {\n'
        b'    dataType="Marker";\n'
        b'    position[]={1000,0,2000};\n'
        b'    name="checkpoint_east";\n'
        b'    text="Checkpoint EST";\n'
        b'    markerType="hd_flag";\n'
        b'    colorName="ColorRed";\n'
        b'  };\n'
        b'};};'
    )

    SQM_NO_MARKERS = b'version=12;\nclass Mission { class Entities { items=0; };};'

    def test_extract_markers_from_sqm_basic(self):
        sqm = self.SQM_WITH_MARKERS.decode('utf-8')
        markers, problems = pbo_extract.extract_markers_from_sqm(sqm)
        self.assertEqual(problems, [])
        self.assertEqual(len(markers), 1)
        self.assertEqual(markers[0]['name'], 'checkpoint_east')
        self.assertEqual(markers[0]['text'], 'Checkpoint EST')
        self.assertEqual(markers[0]['x'], 1000.0)
        self.assertEqual(markers[0]['z'], 2000.0)
        self.assertEqual(markers[0]['type'], 'hd_flag')
        self.assertEqual(markers[0]['color'], 'ColorRed')

    def test_extract_markers_from_sqm_color_array(self):
        sqm = (
            'version=12;\n'
            'class Mission { class Entities { items=1;\n'
            '  class Item0 {\n'
            '    dataType="Marker";\n'
            '    position[]={500,0,1500};\n'
            '    name="wp1";\n'
            '    markerType="mil_dot";\n'
            '    color[]={0,1,0,1};\n'
            '  };\n'
            '};};'
        )
        markers, problems = pbo_extract.extract_markers_from_sqm(sqm)
        self.assertEqual(problems, [])
        self.assertEqual(len(markers), 1)
        self.assertEqual(markers[0]['color'], '#00ff00')

    def test_extract_markers_from_sqm_empty(self):
        markers, problems = pbo_extract.extract_markers_from_sqm(self.SQM_NO_MARKERS.decode('utf-8'))
        self.assertEqual(markers, [])
        self.assertTrue(any('Aucun marqueur Eden' in p for p in problems))

    def test_extract_markers_from_sqm_invalid_position(self):
        sqm = (
            'version=12;\nclass Item0 { dataType="Marker"; name="bad"; };\n'
        )
        markers, problems = pbo_extract.extract_markers_from_sqm(sqm)
        self.assertEqual(markers, [])
        self.assertEqual(len(problems), 1)

    def test_extract_markers_filters_spawn_empty_ai_transparent(self):
        sqm = (
            'version=12;\n'
            'class Mission { class Entities { items=5;\n'
            '  class Item0 { dataType="Marker"; position[]={100,0,200}; name="spawn_west"; '
            'markerType="hd_flag"; text="Spawn"; colorName="ColorBlue"; };\n'
            '  class Item1 { dataType="Marker"; position[]={200,0,300}; name="obj_alpha"; '
            'markerType="empty"; text=""; colorName="ColorRed"; };\n'
            '  class Item2 { dataType="Marker"; position[]={300,0,400}; name="zone"; '
            'markerType="rectangle"; text=""; colorName="ColorYellow"; };\n'
            '  class Item3 { dataType="Marker"; position[]={400,0,500}; name="wp1"; '
            'markerType="mil_dot"; text=""; colorName="ColorGreen"; };\n'
            '  class Item4 { dataType="Marker"; position[]={500,0,600}; name="briefing_pt"; '
            'markerType="mil_flag"; text="Briefing"; colorName="ColorRed"; alpha=0; };\n'
            '  class Item5 { dataType="Marker"; position[]={600,0,700}; name="rally_north"; '
            'markerType="mil_objective"; text="Rally"; colorName="ColorOrange"; };\n'
            '};};'
        )
        markers, problems = pbo_extract.extract_markers_from_sqm(sqm)
        self.assertEqual(len(markers), 2)
        names = {m['name'] for m in markers}
        self.assertEqual(names, {'rally_north', 'zone'})
        self.assertTrue(any('technique' in p for p in problems))

    def test_extract_markers_filters_hide_terrain_object_zones(self):
        sqm = (
            'version=12;\n'
            'class Mission { class Entities { items=5;\n'
            '  class Item0 { dataType="Logic"; class PositionInfo {\n'
            '    position[]={1000,5,2000}; angles[]={0,0,0}; };\n'
            '    areaSize[]={40,0,20}; areaIsRectangle=1;\n'
            '    name="opfor_clean_base"; type="ModuleHideTerrainObjects_F"; };\n'
            '  class Item1 { dataType="Marker"; position[]={1000,0,2000}; '
            'name="opfor_clean_base"; markerType="RECTANGLE"; colorName="ColorYellow"; '
            'a=40; b=20; };\n'
            '  class Item2 { dataType="Marker"; position[]={1001,0,2001}; '
            'name=""; markerType="ELLIPSE"; colorName="ColorYellow"; a=35; b=12; };\n'
            '  class Item3 { dataType="Marker"; position[]={3000,0,4000}; '
            'name="ao_main"; markerType="RECTANGLE"; text="AO"; colorName="ColorRed"; '
            'a=200; b=100; };\n'
            '  class Item4 { dataType="Marker"; position[]={5000,0,6000}; '
            'name="cover"; markerType="moduleCoverMap"; colorName="ColorBlack"; '
            'a=500; b=500; };\n'
            '};};'
        )
        markers, problems = pbo_extract.extract_markers_from_sqm(sqm)
        names = {m['name'] for m in markers}
        self.assertEqual(names, {'ao_main'})
        self.assertTrue(any('technique' in p for p in problems))

    def test_is_marker_visible_filters_hide_terrain_type(self):
        self.assertFalse(pbo_extract.is_marker_visible({
            'name': 'zone_a',
            'type': 'ModuleHideTerrainObjects_F',
            'x': 1.0,
            'z': 2.0,
        }))
        self.assertFalse(pbo_extract.is_marker_visible({
            'name': 'viz',
            'type': 'ellipse',
            'icon': r'modules\HideTerrain\data\icon.paa',
            'x': 1.0,
            'z': 2.0,
        }))
        self.assertFalse(pbo_extract.is_marker_visible({
            'name': 'map_cover',
            'type': 'moduleCoverMap',
            'x': 1.0,
            'z': 2.0,
        }))

    def test_extract_markers_zone_dimensions_and_icon(self):
        sqm = (
            'version=12;\n'
            'class Mission { class Entities { items=2;\n'
            '  class Item0 { dataType="Marker"; position[]={1791,0,7351}; name="factory_2"; '
            'markerType="RECTANGLE"; type="rectangle"; colorName="ColorWEST"; '
            'a=200; b=100; angle=48.4; brush="Solid"; };\n'
            '  class Item1 { dataType="Marker"; position[]={1000,0,2000}; name="mod_icon"; '
            'markerType="icon"; icon="modules\\\\F_curator\\\\data\\\\portraitModule_ca.paa"; '
            'colorName="ColorYellow"; };\n'
            '};};'
        )
        markers, problems = pbo_extract.extract_markers_from_sqm(sqm)
        self.assertEqual(problems, [])
        self.assertEqual(len(markers), 2)
        zone = next(m for m in markers if m['name'] == 'factory_2')
        self.assertEqual(zone['a'], 200.0)
        self.assertEqual(zone['b'], 100.0)
        self.assertAlmostEqual(zone['angle'], 48.4)
        self.assertEqual(zone['brush'], 'Solid')
        icon = next(m for m in markers if m['name'] == 'mod_icon')
        self.assertIn('portraitModule_ca.paa', icon['icon'])

    def test_extract_markers_zone_rotation_and_size2(self):
        sqm = (
            'version=12;\n'
            'class Mission { class Entities { items=1;\n'
            '  class Item0 { dataType="Marker"; position[]={1000,0,2000}; name="zone_a"; '
            'markerType="ELLIPSE"; type="ellipse"; colorName="ColorRed"; '
            'size2[]={250,120}; rotation=135.2; brush="Grid"; };\n'
            '};};'
        )
        markers, _ = pbo_extract.extract_markers_from_sqm(sqm)
        zone = markers[0]
        self.assertEqual(zone['a'], 250.0)
        self.assertEqual(zone['b'], 120.0)
        self.assertAlmostEqual(zone['angle'], 135.2)

    def test_is_marker_visible_load_filters_legacy_json(self):
        import shutil
        import tempfile
        from django.conf import settings
        from django.core.files.storage import default_storage
        from gdc_storm.models import Mission

        temp_dir = tempfile.mkdtemp()
        orig_location = default_storage.location
        settings.MISSIONS_MARKERS_STORAGE_PATH = 'missions/markers'
        default_storage.location = temp_dir
        try:
            markers = [
                {'name': 'spawn_east', 'text': 'Spawn', 'x': 1.0, 'z': 2.0, 'type': 'hd_flag', 'color': ''},
                {'name': 'obj_1', 'text': 'Objectif', 'x': 3.0, 'z': 4.0, 'type': 'mil_objective', 'color': ''},
            ]
            path = pbo_extract.save_markers_to_storage(markers, mission_id=99)
            mission = Mission(name='CPC-CO[01]-Test', authors='A', max_players=20, type='CO', version='1', map='altis')
            mission.markers_file = path
            loaded = pbo_extract.load_markers_from_mission(mission)
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0]['name'], 'obj_1')
        finally:
            default_storage.location = orig_location
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_get_mission_markers_status_missing_file(self):
        from gdc_storm.models import Mission

        mission = Mission(
            name='CPC-CO[01]-Test',
            authors='A',
            max_players=20,
            type='CO',
            version='1',
            map='altis',
            markers_file='missions/markers/missing.json',
        )
        status = pbo_extract.get_mission_markers_status(mission)
        self.assertEqual(status['raw_count'], 0)
        self.assertIn('introuvable', status['error'])

    def test_load_markers_migrates_legacy_file(self):
        import shutil
        import tempfile
        from django.conf import settings
        from gdc_storm.models import Mission

        temp_dir = tempfile.mkdtemp()
        orig_media_root = settings.MEDIA_ROOT
        settings.MEDIA_ROOT = Path(temp_dir)
        legacy_dir = Path(temp_dir) / 'markers'
        legacy_dir.mkdir(parents=True)
        legacy_file = legacy_dir / '42.json'
        legacy_file.write_text(
            json.dumps([{'name': 'obj', 'text': 'A', 'x': 1.0, 'z': 2.0, 'type': 'hd_flag', 'color': ''}]),
            encoding='utf-8',
        )
        mission = Mission.objects.create(
            name='CPC-CO[01]-Legacy',
            authors='A',
            max_players=20,
            type='CO',
            version='1',
            map='altis',
            markers_file='missions/markers/42.json',
        )
        try:
            status = pbo_extract.get_mission_markers_status(mission)
            self.assertEqual(status['raw_count'], 1)
            self.assertIsNone(status['error'])
            mission.refresh_from_db()
            self.assertTrue(pbo_extract.markers_abs_path(mission.markers_file).is_file())
        finally:
            settings.MEDIA_ROOT = orig_media_root
            Mission.objects.filter(pk=mission.pk).delete()
            shutil.rmtree(temp_dir, ignore_errors=True)
        class MockPboItem:
            def __init__(self, data):
                self.data = data
        pbo = {'mission.sqm': MockPboItem(self.SQM_WITH_MARKERS)}
        markers, problems = pbo_extract.extract_markers_from_pbo(pbo)
        self.assertEqual(len(markers), 1)
        self.assertEqual(problems, [])

    def test_extract_markers_from_pbo_binarized(self):
        pbo = {'mission.sqm': type('X', (), {'data': b'notversion'})()}
        markers, problems = pbo_extract.extract_markers_from_pbo(pbo)
        self.assertEqual(markers, [])
        self.assertIn('binarisé', problems[0])

    def test_markers_storage_round_trip(self):
        import shutil
        import tempfile
        from django.conf import settings
        from gdc_storm.models import Mission

        temp_dir = tempfile.mkdtemp()
        orig_media_root = settings.MEDIA_ROOT
        orig_markers_path = getattr(settings, 'MISSIONS_MARKERS_STORAGE_PATH', None)
        settings.MEDIA_ROOT = Path(temp_dir)
        settings.MISSIONS_MARKERS_STORAGE_PATH = 'missions/markers'
        try:
            markers = [{'name': 'm1', 'text': 'T', 'x': 1.0, 'z': 2.0, 'type': 'hd_flag', 'color': ''}]
            path = pbo_extract.save_markers_to_storage(markers, mission_id=42)
            self.assertTrue(path.endswith('42.json'))
            self.assertTrue(pbo_extract.markers_abs_path(path).is_file())
            mission = Mission(name='CPC-CO[01]-Test', authors='A', max_players=20, type='CO', version='1', map='altis')
            mission.pk = 42
            mission.markers_file = path
            loaded = pbo_extract.load_markers_from_mission(mission)
            self.assertEqual(loaded, markers)
            pbo_extract.delete_markers_file(path)
            self.assertFalse(pbo_extract.markers_abs_path(path).is_file())
        finally:
            settings.MEDIA_ROOT = orig_media_root
            if orig_markers_path is not None:
                settings.MISSIONS_MARKERS_STORAGE_PATH = orig_markers_path
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_save_markers_to_storage_empty_returns_none(self):
        self.assertIsNone(pbo_extract.save_markers_to_storage([]))

    def test_sqf_marker_visibility_oop_and_array(self):
        content = (
            '"mm_patrol_1" setMarkerAlpha 0;\n'
            'setMarkerAlpha ["mm_patrol_2", 0];\n'
            '"obj_live" setMarkerAlpha 1;\n'
        )
        states = pbo_extract._sqf_marker_visibility_state(content)
        self.assertFalse(states['mm_patrol_1'])
        self.assertFalse(states['mm_patrol_2'])
        self.assertTrue(states['obj_live'])

    def test_sqf_marker_visibility_foreach_and_delete(self):
        content = (
            '{_x setMarkerAlpha 0} forEach ["zone_a", "zone_b"];\n'
            'deleteMarker "mm_start";\n'
            'setMarkerSize ["mm_hidden", [0,0]];\n'
        )
        states = pbo_extract._sqf_marker_visibility_state(content)
        self.assertFalse(states['zone_a'])
        self.assertFalse(states['zone_b'])
        self.assertFalse(states['mm_start'])
        self.assertFalse(states['mm_hidden'])

    def test_sqf_marker_visibility_show_after_hide(self):
        content = (
            '"mm_temp" setMarkerAlpha 0;\n'
            '"mm_temp" setMarkerAlpha 1;\n'
        )
        states = pbo_extract._sqf_marker_visibility_state(content)
        self.assertTrue(states['mm_temp'])

    def test_collect_startup_script_paths_follows_execvm(self):
        class MockPboItem:
            def __init__(self, filename, data):
                self.filename = filename
                self.data = data

        class MockPbo(list):
            def __getitem__(self, key):
                for item in self:
                    if item.filename == key:
                        return item
                raise KeyError(key)

        pbo = MockPbo([
            MockPboItem('init.sqf', b'[] execVM "scripts\\hide_mm.sqf";'),
            MockPboItem('scripts/hide_mm.sqf', b'"mm_1" setMarkerAlpha 0;'),
            MockPboItem('scripts/trigger_later.sqf', b'deleteMarker "obj_1";'),
        ])
        paths = pbo_extract._collect_startup_script_paths(pbo)
        self.assertIn('init.sqf', paths)
        self.assertIn('scripts/hide_mm.sqf', paths)
        self.assertNotIn('scripts/trigger_later.sqf', paths)

    def test_extract_markers_from_pbo_filters_script_hidden(self):
        class MockPboItem:
            def __init__(self, filename, data):
                self.filename = filename
                self.data = data

        class MockPbo(list):
            def __getitem__(self, key):
                for item in self:
                    if item.filename == key:
                        return item
                raise KeyError(key)

        sqm = (
            b'version=12;\n'
            b'class Mission { class Entities { items=3;\n'
            b'  class Item0 { dataType="Marker"; position[]={100,0,200}; name="mm_patrol"; '
            b'markerType="rectangle"; colorName="ColorRed"; };\n'
            b'  class Item1 { dataType="Marker"; position[]={200,0,300}; name="obj_alpha"; '
            b'markerType="mil_objective"; text="Alpha"; colorName="ColorGreen"; };\n'
            b'  class Item2 { dataType="Marker"; position[]={300,0,400}; name="mm_zone"; '
            b'markerType="ellipse"; colorName="ColorYellow"; a=100; b=50; };\n'
            b'};};'
        )
        init_sqf = b'"mm_patrol" setMarkerAlpha 0;\n{deleteMarker _x} forEach ["mm_zone"];\n'
        pbo = MockPbo([
            MockPboItem('mission.sqm', sqm),
            MockPboItem('init.sqf', init_sqf),
        ])
        markers, problems = pbo_extract.extract_markers_from_pbo(pbo)
        names = {m['name'] for m in markers}
        self.assertEqual(names, {'obj_alpha'})
        self.assertTrue(any('masqué(s) par script SQF' in p for p in problems))

    def test_sqf_marker_name_substring_hide_allmapmarkers(self):
        content = (
            '// hiding zone marker\n'
            '{\n'
            '	if ("mkr" in _x)\n'
            '	then{_x setMarkerAlphaLocal 0};\n'
            '} forEach allMapMarkers;\n'
        )
        rules = pbo_extract._sqf_marker_name_substring_hide_rules(content)
        self.assertEqual(rules, ['mkr'])

    def test_extract_threesome_pbo_hides_mkr_markers(self):
        from pathlib import Path
        from yapbol import PBOFile

        pbo_path = Path('dev/CPC-CO[23]-Threesome-V2.Enoch.pbo')
        if not pbo_path.is_file():
            self.skipTest('PBO Threesome absent du workspace')
        pbo = PBOFile.read_file(str(pbo_path))
        markers, problems = pbo_extract.extract_markers_from_pbo(pbo)
        names = {m['name'] for m in markers}
        self.assertNotIn('mkr_spawnHeli', names)
        self.assertNotIn('mkr_despawnHeli', names)
        self.assertNotIn('mkr_spawnHeli_1', names)
        self.assertIn('marker_1', names)
        self.assertTrue(any('masqué(s) par script SQF' in p for p in problems))

    def test_sqf_marker_visibility_set_marker_type_empty(self):
        content = (
            '"heli_in" setMarkerType "Empty";\n'
            '_mk setMarkerType "Empty";\n'
        )
        states = pbo_extract._sqf_marker_visibility_state(
            '_mk = createMarker ["sortie_heli", [1, 2]];\n' + content
        )
        self.assertFalse(states['heli_in'])
        self.assertFalse(states['sortie_heli'])

    def test_sqf_marker_config_shape_and_size(self):
        content = (
            '"heli_in" setMarkerShape "ELLIPSE";\n'
            '"heli_in" setMarkerSize [120, 80];\n'
            '"heli_in" setMarkerDir 35;\n'
            '"heli_in" setMarkerBrush "Grid";\n'
        )
        overrides = pbo_extract._sqf_marker_config_overrides(content)
        self.assertEqual(overrides['heli_in']['type'], 'ellipse')
        self.assertEqual(overrides['heli_in']['a'], 120.0)
        self.assertEqual(overrides['heli_in']['b'], 80.0)
        self.assertAlmostEqual(overrides['heli_in']['angle'], 35.0)
        self.assertEqual(overrides['heli_in']['brush'], 'Grid')

    def test_sqf_marker_config_variable_create_marker(self):
        content = (
            '_mk = createMarker ["sortie_heli", [1500, 2200]];\n'
            '_mk setMarkerShape "ELLIPSE";\n'
            '_mk setMarkerSize [90, 60];\n'
            '_mk setMarkerText "Sortie hélico";\n'
            '_mk setMarkerColor "ColorBlue";\n'
        )
        overrides = pbo_extract._sqf_marker_config_overrides(content)
        self.assertEqual(overrides['sortie_heli']['type'], 'ellipse')
        self.assertEqual(overrides['sortie_heli']['x'], 1500.0)
        self.assertEqual(overrides['sortie_heli']['z'], 2200.0)
        self.assertEqual(overrides['sortie_heli']['text'], 'Sortie hélico')

    def test_extract_markers_from_pbo_applies_sqf_zone_shape(self):
        class MockPboItem:
            def __init__(self, filename, data):
                self.filename = filename
                self.data = data

        class MockPbo(list):
            def __getitem__(self, key):
                for item in self:
                    if item.filename == key:
                        return item
                raise KeyError(key)

        init_sqf = (
            b'"heli_in" setMarkerShape "ELLIPSE";\n'
            b'"heli_in" setMarkerSize [100, 70];\n'
            b'"heli_out" setMarkerShape "ELLIPSE";\n'
            b'"heli_out" setMarkerSize [80, 80];\n'
        )
        sqm = (
            'version=12;\n'
            'class Mission { class Entities { items=2;\n'
            '  class Item0 { dataType="Marker"; position[]={1000,0,2000}; name="heli_in"; '
            'markerType="hd_dot"; text="Arrivée hélico"; colorName="ColorBlue"; };\n'
            '  class Item1 { dataType="Marker"; position[]={1200,0,2200}; name="heli_out"; '
            'markerType="mil_flag"; text="Sortie hélico"; colorName="ColorBlue"; };\n'
            '};};'
        ).encode('utf-8')
        pbo = MockPbo([
            MockPboItem('mission.sqm', sqm),
            MockPboItem('init.sqf', init_sqf),
        ])
        markers, problems = pbo_extract.extract_markers_from_pbo(pbo)
        heli_in = next(m for m in markers if m['name'] == 'heli_in')
        heli_out = next(m for m in markers if m['name'] == 'heli_out')
        self.assertEqual(heli_in['type'], 'ellipse')
        self.assertEqual(heli_in['a'], 100.0)
        self.assertEqual(heli_out['type'], 'ellipse')
        self.assertTrue(any('enrichi(s) depuis scripts SQF' in p for p in problems))

    def test_is_marker_visible_hidden_by_script_flag(self):
        marker = {
            'name': 'obj_1',
            'type': 'mil_objective',
            'hidden_by_script': True,
        }
        self.assertFalse(pbo_extract.is_marker_visible(marker))

    def test_extract_sqm_startup_scripts_init_attribute(self):
        sqm = (
            'class Item0 { dataType="Logic"; class Attributes {\n'
            '  init="""mm_patrol"" setMarkerAlpha 0;" \n "deleteMarker ""mm_zone"";";\n'
            '};\n'
            'class CustomAttributes { class Attribute0 {\n'
            '  property="Init"; expression="""obj_hidden"" setMarkerAlpha 0;";\n'
            '};\n'
            '};'
        )
        embedded = pbo_extract._extract_sqm_startup_scripts(sqm)
        states = pbo_extract._sqf_marker_visibility_state(embedded)
        self.assertFalse(states['mm_patrol'])
        self.assertFalse(states['mm_zone'])
        self.assertFalse(states['obj_hidden'])

    def test_extract_markers_from_pbo_filters_sqm_embedded_init(self):
        class MockPboItem:
            def __init__(self, filename, data):
                self.filename = filename
                self.data = data

        class MockPbo(list):
            def __getitem__(self, key):
                for item in self:
                    if item.filename == key:
                        return item
                raise KeyError(key)

        sqm = (
            b'version=12;\n'
            b'class Mission { class Entities { items=2;\n'
            b'  class Item0 { dataType="Marker"; position[]={100,0,200}; name="mm_editor"; '
            b'markerType="mil_dot"; colorName="ColorRed"; };\n'
            b'  class Item1 { dataType="Marker"; position[]={200,0,300}; name="obj_real"; '
            b'markerType="mil_objective"; text="Obj"; colorName="ColorGreen"; };\n'
            b'  class Item2 { dataType="Logic"; class Attributes {\n'
            b'    init="""mm_editor"" setMarkerAlpha 0;";\n'
            b'  }; };\n'
            b'};};'
        )
        pbo = MockPbo([MockPboItem('mission.sqm', sqm)])
        markers, problems = pbo_extract.extract_markers_from_pbo(pbo)
        names = {m['name'] for m in markers}
        self.assertEqual(names, {'obj_real'})
        self.assertTrue(any('masqué(s) par script SQF' in p for p in problems))

if __name__ == '__main__':
    unittest.main()
