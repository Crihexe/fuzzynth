import unittest

from pathlib import Path

from fuzzynth.campaign_config import load_campaign_configuration
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

    def test_prompt_variants_receive_identical_pair_seed(self):
        configuration = load_campaign_configuration(
            Path("config/campaign-workers.toml")
        )
        rich = configuration.workers[
            "gpt-4o-mini-official-temperature-js-rich"
        ]
        lean = configuration.workers[
            "gpt-4o-mini-official-temperature-js-lean"
        ]

        self.assertEqual(rich.corpus_pair_id, lean.corpus_pair_id)
        self.assertEqual(
            _stable_seed(20260902, rich.corpus_pair_id, 17),
            _stable_seed(20260902, lean.corpus_pair_id, 17),
        )


if __name__ == "__main__":
    unittest.main()
