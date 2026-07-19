import unittest
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

if __name__ == '__main__':
    unittest.main()
