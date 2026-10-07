#!/usr/bin/env python3
"""The hover card reads only fields the engine's card really has.

Runs the real engine (`card`) on a made-up fixture, then finds every field
qml/HoverCard.qml reads from the document and asserts each exists in that
card. A renamed or misspelled field would otherwise paint an empty string
silently: QML reads a missing key as undefined, and qmllint cannot see it.

    python3 -m unittest discover -s tests
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ENGINE = os.environ.get(
    "SCREEN_TIME_ENGINE", os.path.join(ROOT, "python", "screen_time.py")
)
CARD_QML = os.environ.get(
    "SCREEN_TIME_CARD_QML", os.path.join(ROOT, "qml", "HoverCard.qml")
)
sys.path.insert(0, HERE)

import oracle_screen_time as oracle


def engine_card():
    with tempfile.TemporaryDirectory() as tmp:
        hist = os.path.join(tmp, "history.json")
        doc = oracle.busy_history()
        # D42: today recorded whole by hour, so the Day's 24 bars exist
        today = doc["days"]["2025-06-11"]
        today["hours"] = [0] * 24
        today["hours"][14] = today["total"]
        with open(hist, "w") as f:
            json.dump(doc, f)
        p = subprocess.run(
            [sys.executable, "-I", ENGINE, "card", "--history", hist,
             "--names", os.path.join(tmp, "names.json"), "--as-of", "2025-06-11T15:00:00"],
            capture_output=True, text=True, env=dict(os.environ, TZ="America/Chicago", HOME=tmp),
            timeout=60, check=True,
        )  # fmt: skip
    return json.loads(p.stdout)


# (pattern in HoverCard.qml, the objects that must each hold the field). A shared
# component (the header, a period page, the plot) reads every page it can show.
PERIODS = ("week", "month", "year")
READS = [
    (r"\bhc\.doc\.(\w+)", lambda c: [c]),
    (r"\bhc\.today\.(\w+)", lambda c: [c["today"]]),
    (r"\bhc\.footer\.(\w+)", lambda c: [c["footer"]]),
    (
        r"\brow\.modelData\.(\w+)",
        lambda c: (
            [c["rows"][0], c["day"]["rows"][0]] + [c[k]["rows"][0] for k in PERIODS]
        ),
    ),
    (
        r"\bsegment\.modelData\.(\w+)",
        lambda c: (
            [c["segments"][0]] + [c[k]["segments"][0] for k in ("day",) + PERIODS]
        ),
    ),
    # V1: the Day page, any day
    (r"\bhc\.dayPage\.(\w+)", lambda c: [c["day"]]),
    (r"\bhc\.dayPage\.usual\.(\w+)", lambda c: [c["day"]["usual"]]),
    # D42: the Day's hourly chart, painted by the shared plot
    (r"\bhc\.dayHours\.(\w+)", lambda c: [c["day"]["hours"]]),
    # the header reads every detail page; the period pages, plot and bars the three
    (r"\bheader\.view\.(\w+)", lambda c: [c[k] for k in ("day",) + PERIODS]),
    (r"\bhc\.weekPage\.(\w+)", lambda c: [c["week"]]),
    (r"\bhc\.monthPage\.(\w+)", lambda c: [c["month"]]),
    (r"\bhc\.yearPage\.(\w+)", lambda c: [c["year"]]),
    (r"\bperiodPage\.view\.(\w+)", lambda c: [c[k] for k in PERIODS]),
    # D38: one line component paints every page's line
    (r"\bpageLine\.line\.(\w+)", lambda c: [c[k]["line"] for k in ("day",) + PERIODS]),
    (
        r"\bplotFrame\.view\.(\w+)",
        lambda c: [c[k] for k in PERIODS] + [c["day"]["hours"]],
    ),
    # D43: the scale's ticks on every chart the plot paints
    (
        r"\btick\.modelData\.(\w+)",
        lambda c: [c[k]["ticks"][0] for k in PERIODS] + [c["day"]["hours"]["ticks"][0]],
    ),
    (
        r"\bperiodBar\.modelData\.(\w+)",
        lambda c: [c[k]["bars"][0] for k in PERIODS] + [c["day"]["hours"]["bars"][0]],
    ),
]


class CardContractTests(unittest.TestCase):
    def test_every_field_the_card_reads_exists(self):
        card = engine_card()
        self.assertEqual(card["state"], "ok")
        with open(CARD_QML) as f:
            qml = f.read()
        checked = 0
        for pattern, reach in READS:
            for field in sorted(set(re.findall(pattern, qml))):
                for holder in reach(card):
                    checked += 1
                    self.assertIn(
                        field,
                        holder,
                        f"HoverCard reads {pattern} -> {field!r}, not in the card",
                    )
        self.assertGreaterEqual(
            checked, 15, "the scan found too few reads to mean anything"
        )

    def test_the_card_reads_no_dropped_field(self):
        with open(CARD_QML) as f:
            qml = f.read()
        self.assertNotIn("chips", qml)  # D11
        self.assertNotIn("footer.text", qml)  # D19: the footer's week left the card
        self.assertNotIn("week_text", qml)
        # V1: one page per kind; the lists of pages and their indexes are gone
        for gone in (
            "years",
            "year_index",
            "weekIndex",
            "yearIndex",
            "start_key",
            "letter",
            "usual",  # D38: the comparison is with the all-time average
            "y_max_text",  # D38: no scale label on the charts
            ".head",  # D41: the line is only the comparison
        ):
            self.assertNotIn(gone, qml)


if __name__ == "__main__":
    unittest.main()
