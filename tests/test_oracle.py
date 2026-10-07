#!/usr/bin/env python3
"""Runs the independent oracle (tests/oracle_screen_time.py) as part of

    python3 -m unittest discover -s tests

so every commit checks the whole card against it. The mutation run
(tools/mutants.py) is separate: it takes about half a minute.
"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.environ.get(
    "SCREEN_TIME_ENGINE",
    os.path.join(os.path.dirname(HERE), "python", "screen_time.py"),
)
sys.path.insert(0, HERE)

import oracle_screen_time as oracle


class OracleTests(unittest.TestCase):
    def test_engine_matches_the_oracle(self):
        fails = oracle.check(ENGINE)
        self.assertEqual(fails, [], "\n".join(fails[:20]))

    def test_the_oracle_agrees_with_the_hand_pinned_values(self):
        # The oracle's own card for the hand-pinned scenario must carry the
        # values worked out by hand, or a shared mistake could pass.
        card = oracle.expected_card(oracle.PINNED)
        for path, value in oracle.PINNED_EXPECT:
            got = oracle.pinned_value(card, path)
            if isinstance(value, float):
                self.assertAlmostEqual(got, value, places=12, msg=path)
            else:
                self.assertEqual(got, value, path)


if __name__ == "__main__":
    unittest.main()
