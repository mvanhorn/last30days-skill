import unittest

import last30days as cli


def _parse(*argv: str):
    parser = cli.build_parser()
    args, _extra = parser.parse_known_args(argv)
    return args


class HiringSignalsCliTests(unittest.TestCase):
    def test_hiring_signals_default_disabled(self):
        args = _parse("Listen Labs")
        self.assertFalse(args.hiring_signals)

    def test_hiring_signals_flag_enabled(self):
        args = _parse("Listen Labs", "--hiring-signals")
        self.assertTrue(args.hiring_signals)

    def test_jobs_search_source_is_valid(self):
        self.assertEqual(["jobs"], cli.parse_search_flag("jobs"))


if __name__ == "__main__":
    unittest.main()
