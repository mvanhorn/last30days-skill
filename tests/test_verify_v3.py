import importlib.util
import unittest
from pathlib import Path


def load_verify_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "verify_v3.py"
    spec = importlib.util.spec_from_file_location("verify_v3_module", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class VerifyV3Tests(unittest.TestCase):
    def test_parser_defaults(self):
        module = load_verify_module()
        parser = module.build_parser()
        args = parser.parse_args([])
        self.assertEqual(args.baseline, "HEAD~1")
        self.assertEqual(args.candidate, "WORKTREE")
        self.assertFalse(args.skip_eval)
        self.assertFalse(args.skip_latency)


if __name__ == "__main__":
    unittest.main()
