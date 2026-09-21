import unittest

from nimbus_api.shortener import decode, encode


class ShortenerTest(unittest.TestCase):
    def test_roundtrip(self):
        for n in [0, 1, 61, 62, 1000, 123456789]:
            self.assertEqual(decode(encode(n)), n)

    def test_encode_zero(self):
        # A zero id must still produce a usable one-character code, not "".
        self.assertEqual(encode(0), "0")

    def test_encode_known_values(self):
        self.assertEqual(encode(1), "1")
        self.assertEqual(encode(61), "Z")
        self.assertEqual(encode(62), "10")

    def test_decode_rejects_invalid(self):
        with self.assertRaises(ValueError):
            decode("!!")


if __name__ == "__main__":
    unittest.main()
