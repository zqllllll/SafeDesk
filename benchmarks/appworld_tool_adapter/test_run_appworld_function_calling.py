import importlib.util
import os
import unittest
from pathlib import Path


RUNNER_PATH = Path(__file__).with_name("run_appworld_function_calling.py")
SPEC = importlib.util.spec_from_file_location("appworld_runner", RUNNER_PATH)
assert SPEC and SPEC.loader
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)
RUNNER._add_paths()


class RunnerAlignmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from appworld import AppWorld, load_task_ids

        cls.world = AppWorld(
            task_id=load_task_ids("test_challenge")[0],
            experiment_name="runner_alignment_test",
            ground_truth_mode="minimal",
            raise_on_failure=False,
            raise_on_extra_parameters=True,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.world.close()

    def test_schema_inventory_matches_task_api_docs(self) -> None:
        local_names = {RUNNER._tool_name(item) for item in RUNNER._load_all_tool_schemas()}
        task_names = {
            item["function"]["name"]
            for item in self.world.task.api_docs.function_calling()
        }
        self.assertEqual(local_names, task_names)

    def test_predictor_prompt_excludes_api_docs(self) -> None:
        messages = RUNNER._api_predictor_messages(self.world.task)
        header = messages[0]["content"]
        self.assertNotIn("api_docs:", header)
        self.assertIn("supervisor:", header)

    def test_parser_is_strict_and_always_includes_complete_task(self) -> None:
        schemas = RUNNER._load_all_tool_schemas()
        predicted, tools = RUNNER._parse_predicted_apis(
            "Use spotify.login\nspotify.login\nspotify.search_songs\n",
            schemas,
            max_predicted_apis=20,
        )
        self.assertEqual(
            predicted,
            ["supervisor.complete_task", "spotify.login", "spotify.search_songs"],
        )
        self.assertEqual(
            [RUNNER._tool_name(item) for item in tools],
            ["supervisor__complete_task", "spotify__login", "spotify__search_songs"],
        )

    def test_out_of_schema_result_is_explicitly_non_executed(self) -> None:
        result = RUNNER._out_of_schema_result("spotify__search_songs")
        self.assertFalse(result["executed"])
        self.assertEqual(result["reason"], "out_of_schema_tool_call")


if __name__ == "__main__":
    unittest.main()
