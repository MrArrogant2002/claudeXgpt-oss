import unittest

from nimbus_api.store import LinkError, LinkStore


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.store = LinkStore(":memory:")

    def test_create_and_resolve(self):
        code = self.store.create_link("https://example.com")
        self.assertEqual(self.store.resolve(code), "https://example.com")

    def test_records_clicks(self):
        code = self.store.create_link("https://example.com/a")
        self.store.record_click(code)
        self.store.record_click(code)
        self.assertEqual(self.store.stats(code)["clicks"], 2)

    def test_rejects_bad_url(self):
        with self.assertRaises(LinkError):
            self.store.create_link("nope")

    def test_unknown_code_raises(self):
        with self.assertRaises(LinkError):
            self.store.resolve("zzzz")


if __name__ == "__main__":
    unittest.main()
