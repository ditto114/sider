import unittest
from src.utils import calculate_relative_coordinates

class TestUtils(unittest.TestCase):
    def test_center(self):
        # 100x100 region. Center is 50, 50.
        # Match at 50, 50. Relative should be 0, 0.
        x, y = calculate_relative_coordinates(100, 100, 50, 50)
        self.assertEqual(x, 0)
        self.assertEqual(y, 0)

    def test_top_right(self):
        # 100x100. Center 50, 50.
        # Match at 100, 0 (Top Right on screen).
        # Relative X: 100 - 50 = 50
        # Relative Y: 50 - 0 = 50 (Up is positive)
        x, y = calculate_relative_coordinates(100, 100, 100, 0)
        self.assertEqual(x, 50)
        self.assertEqual(y, 50)

    def test_bottom_left(self):
        # 100x100. Center 50, 50.
        # Match at 0, 100 (Bottom Left on screen).
        # Relative X: 0 - 50 = -50
        # Relative Y: 50 - 100 = -50 (Down is negative)
        x, y = calculate_relative_coordinates(100, 100, 0, 100)
        self.assertEqual(x, -50)
        self.assertEqual(y, -50)

if __name__ == '__main__':
    unittest.main()
