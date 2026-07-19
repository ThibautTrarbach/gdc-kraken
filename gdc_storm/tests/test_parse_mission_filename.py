import unittest
from gdc_storm.utils import (
    parse_mission_filename,
    parse_mission_filename_lenient,
    recup_parse_mission_filename,
)

class TestParseMissionFilename(unittest.TestCase):
    def test_valid_filename(self):
        filename = 'CPC-CO[20]-TestMission-V2.altis.pbo'
        result = parse_mission_filename(filename)
        self.assertIsNotNone(result)
        self.assertEqual(result[0], 'CPC-CO[20]-TestMission')
        self.assertEqual(result[1], 'CO')
        self.assertEqual(result[2], '20')
        self.assertEqual(result[3], 'V2')
        self.assertEqual(result[4], 'altis')

    def test_invalid_filename(self):
        invalid_filenames = [
            'badname.pbo',
            'CPC-CO[20]-TestMission-V2.pbo',
            'CPC-CO[20]-TestMission-V2.altis',
            'CPC-CO[20]-TestMission-V2.altis.pbo.extra',
            'CPC-CO[1]-TestMission-V2.altis.pbo',
        ]
        for filename in invalid_filenames:
            with self.subTest(filename=filename):
                result = parse_mission_filename(filename)
                self.assertIsNone(result)

    def test_wrong_type(self):
        filename = 'CPC-XX[20]-TestMission-V2.altis.pbo'
        result = parse_mission_filename(filename)
        self.assertIsNone(result)

    def test_missing_version(self):
        filename = 'CPC-CO[20]-TestMission.altis.pbo'
        result = parse_mission_filename(filename)
        self.assertIsNone(result)

    def test_three_digit_players(self):
        filename = 'CPC-CO[100]-BigMission-V1.altis.pbo'
        result = parse_mission_filename(filename)
        self.assertIsNotNone(result)
        self.assertEqual(result[2], '100')

    def test_strict_rejects_hc_tag(self):
        filename = 'CPC-CO[09]-Blind_Faith-v9-HC.porto.pbo'
        self.assertIsNone(parse_mission_filename(filename))

    def test_strict_rejects_underscore_version(self):
        filename = 'CPC-CO[12]-PMC_Blood_Rain_V2.Stratis.pbo'
        self.assertIsNone(parse_mission_filename(filename))


class TestParseMissionFilenameLenient(unittest.TestCase):
    def test_tag_hc_suffix(self):
        filename = 'CPC-CO[09]-Blind_Faith-v9-HC.porto.pbo'
        result = parse_mission_filename_lenient(filename)
        self.assertIsNotNone(result)
        self.assertEqual(result[0], 'CPC-CO[09]-Blind_Faith')
        self.assertEqual(result[3], 'v9')
        self.assertEqual(result[4], 'porto')

    def test_tag_hc_parentheses(self):
        filename = 'CPC-CO[09]-Sandstorm01-v6-(HC).Zargabad.pbo'
        result = parse_mission_filename_lenient(filename)
        self.assertIsNotNone(result)
        self.assertEqual(result[0], 'CPC-CO[09]-Sandstorm01')
        self.assertEqual(result[3], 'v6')
        self.assertEqual(result[4], 'Zargabad')

    def test_underscore_before_version(self):
        filename = 'CPC-CO[12]-PMC_Blood_Rain_V2.Stratis.pbo'
        result = parse_mission_filename_lenient(filename)
        self.assertIsNotNone(result)
        self.assertEqual(result[0], 'CPC-CO[12]-PMC_Blood_Rain')
        self.assertEqual(result[3], 'V2')
        self.assertEqual(result[4], 'Stratis')

    def test_dash_underscore_before_version(self):
        filename = 'CPC-CO[11]-Video_Killed_The_Radio_Star_-_v5.gsep_zernovo.pbo'
        result = parse_mission_filename_lenient(filename)
        self.assertIsNotNone(result)
        self.assertEqual(result[0], 'CPC-CO[11]-Video_Killed_The_Radio_Star')
        self.assertEqual(result[3], 'v5')
        self.assertEqual(result[4], 'gsep_zernovo')

    def test_lenient_still_requires_version(self):
        self.assertIsNone(parse_mission_filename_lenient('CPC-CO[10]-Cheval_de_troie.Chernarus_Summer.pbo'))


class TestRecupParseMissionFilename(unittest.TestCase):
    def test_strict_still_ok(self):
        parsed, relaxed = recup_parse_mission_filename('CPC-CO[20]-TestMission-V2.altis.pbo')
        self.assertFalse(relaxed)
        self.assertEqual(parsed[0], 'CPC-CO[20]-TestMission')

    def test_lenient_hc_via_recup(self):
        parsed, relaxed = recup_parse_mission_filename('CPC-CO[09]-Blind_Faith-v9-HC.porto.pbo')
        self.assertTrue(relaxed)
        self.assertEqual(parsed[0], 'CPC-CO[09]-Blind_Faith')
        self.assertEqual(parsed[3], 'v9')

    def test_missing_version(self):
        parsed, relaxed = recup_parse_mission_filename('CPC-CO[20]-TestMission.altis.pbo')
        self.assertTrue(relaxed)
        self.assertEqual(parsed[0], 'CPC-CO[20]-TestMission')
        self.assertEqual(parsed[3], 'V1')
        self.assertEqual(parsed[4], 'altis')

    def test_freeform_name(self):
        parsed, relaxed = recup_parse_mission_filename('MyWeirdMission.altis.pbo')
        self.assertTrue(relaxed)
        self.assertEqual(parsed[0], 'MyWeirdMission')
        self.assertEqual(parsed[1], 'CO')
        self.assertEqual(parsed[4], 'altis')


if __name__ == '__main__':
    unittest.main()
