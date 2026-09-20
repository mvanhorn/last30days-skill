import importlib.util
import unittest
from pathlib import Path
from unittest import mock


def load_verify_module():
    path = Path(__file__).resolve().parents[1] / "skills" / "nfl30" / "scripts" / "verify_v3.py"
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

    def test_smoke_and_latency_request_raw_json_profile(self):
        module = load_verify_module()
        completed = mock.Mock(stdout='{"clusters": [], "ranked_candidates": []}')
        with mock.patch.object(module, "run_command", return_value=completed) as run:
            module.SMOKE_CASES = [("auto", ["--quick"])]
            module.LATENCY_PROFILES = [("quick", ["--quick"])]
            module.LATENCY_TOPICS = ["topic"]
            module.verify_smoke()
            module.verify_latency()

        commands = [call.args[0] for call in run.call_args_list]
        self.assertTrue(all("--json-profile=raw" in command for command in commands))

    def test_unit_stage_invokes_pytest_runner(self):
        module = load_verify_module()
        rg_files = mock.Mock(stdout="skills/nfl30/scripts/verify_v3.py\n")
        with mock.patch.object(module, "run_command") as run, mock.patch.object(
            module.subprocess, "run", return_value=rg_files
        ):
            module.verify_unit()

        runner_command = run.call_args_list[0].args[0]
        self.assertEqual(runner_command, [module.PYTHON, "-m", "pytest", "tests"])
        self.assertNotIn("unittest", runner_command)


if __name__ == "__main__":
    unittest.main()
