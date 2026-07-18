import unittest
from gdc_storm.utils import parse_mission_filename, recup_parse_mission_filename

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


class TestRecupParseMissionFilename(unittest.TestCase):
    def test_strict_still_ok(self):
        parsed, relaxed = recup_parse_mission_filename('CPC-CO[20]-TestMission-V2.altis.pbo')
        self.assertFalse(relaxed)
        self.assertEqual(parsed[0], 'CPC-CO[20]-TestMission')

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
