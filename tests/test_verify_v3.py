import importlib.util
import unittest
from pathlib import Path
from unittest import mock


def load_verify_module():
    path = Path(__file__).resolve().parents[1] / "skills" / "last30days" / "scripts" / "verify_v3.py"
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
        completed = mock.Mock(stdout='{"clusters": [{"id":"c1"}], "ranked_candidates": [{"id":"r1"}], "provider_runtime":{"reasoning_provider":"fixture"}, "errors_by_source":{"reddit":"fixture-error"}}')
        with mock.patch.object(module, "run_command", return_value=completed) as run:
            module.SMOKE_CASES = [("auto", ["--quick"])]
            module.LATENCY_PROFILES = [("quick", ["--quick"])]
            module.LATENCY_TOPICS = ["topic"]
            smoke = module.verify_smoke()
            latency = module.verify_latency()

        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(len(commands), 2)
        self.assertEqual(commands[0], [module.PYTHON, str(module.ENGINE), module.SMOKE_TOPIC, "--emit=json", "--json-profile=raw", "--quick"])
        self.assertEqual(commands[1], [module.PYTHON, str(module.ENGINE), "topic", "--emit=json", "--json-profile=raw", "--quick"])
        self.assertEqual(run.call_args_list[0].kwargs["env"]["LAST30DAYS_REASONING_PROVIDER"], "auto")
        self.assertEqual(len(smoke), 1)
        self.assertEqual({key: value for key, value in smoke[0].items() if key != "duration_seconds"}, {
            "provider": "auto", "reasoning_provider": "fixture", "cluster_count": 1,
            "candidate_count": 1, "error_sources": ["reddit"],
        })
        self.assertEqual(set(latency), {"quick"})
        self.assertEqual(len(latency["quick"]["times"]), 1)
        self.assertTrue(all("--json-profile=raw" in command for command in commands))

    def test_unit_stage_invokes_pytest_runner(self):
        module = load_verify_module()
        rg_files = mock.Mock(stdout="skills/last30days/scripts/verify_v3.py\n")
        with mock.patch.object(module, "run_command") as run, mock.patch.object(
            module.subprocess, "run", return_value=rg_files
        ):
            module.verify_unit()

        runner_command = run.call_args_list[0].args[0]
        self.assertEqual(runner_command, [module.PYTHON, "-m", "pytest", "tests"])
        self.assertNotIn("unittest", runner_command)


if __name__ == "__main__":
    unittest.main()
