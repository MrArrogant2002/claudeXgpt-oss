import unittest

from nimbus_api.validators import is_valid_url, normalize_url


class ValidatorsTest(unittest.TestCase):
    def test_accepts_http_and_https(self):
        self.assertTrue(is_valid_url("https://example.com/a?b=c"))
        self.assertTrue(is_valid_url("http://x.io"))

    def test_rejects_bad_input(self):
        self.assertFalse(is_valid_url("ftp://example.com"))
        self.assertFalse(is_valid_url("not a url"))
        self.assertFalse(is_valid_url(""))

    def test_normalize_lowercases_host_only(self):
        self.assertEqual(
            normalize_url("https://EXAMPLE.com/Path"), "https://example.com/Path"
        )


if __name__ == "__main__":
    unittest.main()
