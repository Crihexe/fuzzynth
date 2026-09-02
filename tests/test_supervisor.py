import unittest

from fuzzynth.supervisor import _stable_seed


class SupervisorSeedTests(unittest.TestCase):
    def test_stable_seed_always_fits_sqlite_signed_integer(self):
        seed = _stable_seed(
            20260902,
            "luna-official-high-temperature-js",
            1,
        )

        self.assertEqual(seed, 787_664_373_707_780_112)
        self.assertGreaterEqual(seed, 0)
        self.assertLessEqual(seed, (1 << 63) - 1)

    def test_stable_seed_is_reproducible_and_varies_by_ordinal(self):
        first = _stable_seed(7, "worker", 1)

        self.assertEqual(first, _stable_seed(7, "worker", 1))
        self.assertNotEqual(first, _stable_seed(7, "worker", 2))


if __name__ == "__main__":
    unittest.main()
