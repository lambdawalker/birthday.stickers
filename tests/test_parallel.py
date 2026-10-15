import _thread
import json
import tempfile
import threading
import unittest
from pathlib import Path
from queue import Empty, Queue
from unittest.mock import patch

import test_pipeline as fixtures

from birthday_stickers.comfy import ComfyClient
from birthday_stickers.control import RunControl, RunHalted
from birthday_stickers.engine import Pipeline
from birthday_stickers.llm import LLMClient
from birthday_stickers.pipeline import run, scene_records
from birthday_stickers.sessions import SessionStore, session_lock


class ParallelTests(unittest.TestCase):
    def test_all_prompts_can_finish_while_first_image_is_still_rendering(self):
        with tempfile.TemporaryDirectory() as temp, fixtures.FakeServices() as service:
            config = fixtures.PipelineTests().configuration(temp, service, count=3)
            rendering = threading.Event()
            prompts_done = threading.Event()
            original_llm, original_wait = LLMClient.complete, ComfyClient.wait_image
            planning_calls = 0
            ids = {}
            updates = []

            def llm(client, prompt, *, refined=False):
                nonlocal planning_calls
                ids["llm"] = threading.get_ident()
                if not refined:
                    planning_calls += 1
                    if planning_calls == 2:
                        self.assertTrue(rendering.wait(5), "Image worker never started")
                return original_llm(client, prompt, refined=refined)

            def wait(client, prompt_id, output_id, checkpoint=lambda: None):
                ids["images"] = threading.get_ident()
                if prompt_id == "job-1":
                    rendering.set()
                    self.assertTrue(prompts_done.wait(5), "LLM waited for the first image")
                    self.assertEqual(len(service.chats), 6)
                return original_wait(client, prompt_id, output_id, checkpoint)

            def report(lane, done, name, stage):
                updates.append((lane, done, stage))
                if lane == "prompts" and done == 3:
                    prompts_done.set()

            with (
                patch.object(LLMClient, "complete", llm),
                patch.object(ComfyClient, "wait_image", wait),
            ):
                result = run(config, worker_progress=report)
            self.assertEqual(len(result), 3)
            self.assertNotEqual(ids["llm"], ids["images"])
            for lane in ("prompts", "images"):
                counts = [done for worker, done, _ in updates if worker == lane]
                self.assertEqual(counts, sorted(counts))
                self.assertEqual(counts[-1], 3)
            for _, meta in scene_records(config.output.directory):
                timings = meta["timings"]
                self.assertTrue(
                    all(t["seconds"] >= 0 and t["outcome"] == "complete" for t in timings)
                )
                self.assertEqual(
                    {t["step"] for t in timings},
                    {
                        "planning",
                        "refining",
                        "upload_references",
                        "rendering_qwen",
                        "save_intermediate",
                        "upload_intermediate",
                        "rendering_flux",
                        "save_final",
                    },
                )

    def test_pause_saves_prompt_backlog_and_resumes_without_duplicate_jobs(self):
        with tempfile.TemporaryDirectory() as temp, fixtures.FakeServices() as service:
            config = fixtures.PipelineTests().configuration(temp, service, count=3)
            control = RunControl()
            rendering = threading.Event()
            original_wait = ComfyClient.wait_image
            original_llm = LLMClient.complete
            plans = 0

            def llm(client, prompt, *, refined=False):
                nonlocal plans
                if not refined:
                    plans += 1
                    if plans == 2:
                        self.assertTrue(rendering.wait(5))
                return original_llm(client, prompt, refined=refined)

            def wait(client, prompt_id, output_id, checkpoint=lambda: None):
                rendering.set()
                self.assertTrue(control.requested.wait(5))
                return original_wait(client, prompt_id, output_id, checkpoint)

            def report(lane, done, name, stage):
                if lane == "prompts" and done == 3:
                    self.assertTrue(rendering.is_set())
                    control.request()

            with (
                patch.object(ComfyClient, "wait_image", wait),
                patch.object(LLMClient, "complete", llm),
                self.assertRaises(RunHalted),
            ):
                run(config, resume=True, control=control, worker_progress=report)
            records = scene_records(config.output.directory)
            self.assertEqual(len(records), 3)
            self.assertTrue(all("refined_scene" in meta for _, meta in records))
            self.assertEqual(len(service.chats), 6)
            self.assertEqual(len(service.graphs), 1)
            self.assertEqual(records[0][1]["timings"][-1]["outcome"], "paused")
            result = run(config, resume=True)
            self.assertEqual(len(result), 3)
            self.assertEqual(len(service.chats), 6)
            self.assertEqual(len(service.graphs), 6)
            attempts = [
                t
                for t in scene_records(config.output.directory)[0][1]["timings"]
                if t["step"] == "rendering_qwen"
            ]
            self.assertEqual([t["outcome"] for t in attempts], ["paused", "complete"])

    def test_image_failure_waits_for_inflight_llm_checkpoint_and_reports_original_error(self):
        with tempfile.TemporaryDirectory() as temp, fixtures.FakeServices() as service:
            config = fixtures.PipelineTests().configuration(temp, service, count=3)
            second_planning = threading.Event()
            image_failed = threading.Event()
            original_llm = LLMClient.complete
            plans = 0

            def llm(client, prompt, *, refined=False):
                nonlocal plans
                if not refined:
                    plans += 1
                    if plans == 2:
                        second_planning.set()
                        self.assertTrue(image_failed.wait(5))
                return original_llm(client, prompt, refined=refined)

            def make_pipeline(*args):
                pipeline = Pipeline(*args)
                # Release the in-flight LLM only after the worker records its failure.
                pipeline.abort = image_failed
                return pipeline

            def wait(*args):
                self.assertTrue(second_planning.wait(5))
                raise RuntimeError("image service disconnected")

            with (
                patch("birthday_stickers.engine.Pipeline", side_effect=make_pipeline),
                patch.object(LLMClient, "complete", llm),
                patch.object(ComfyClient, "wait_image", wait),
            ):
                with self.assertRaisesRegex(RuntimeError, "image service disconnected"):
                    run(config, resume=True)
            records = scene_records(config.output.directory)
            self.assertEqual(len(records), 2)
            self.assertIn("raw_scene", records[1][1])
            self.assertEqual(records[1][1]["status"], "interrupted")
            self.assertEqual(records[0][1]["qwen_prompt_id"], "job-1")
            self.assertFalse(
                any(t.name in ("sticker-prompts", "sticker-images") for t in threading.enumerate())
            )

    def test_queue_timeout_does_not_skip_last_prompt(self):
        with tempfile.TemporaryDirectory() as temp, fixtures.FakeServices() as service:
            config = fixtures.PipelineTests().configuration(temp, service)
            allow_prompt = threading.Event()
            producer_finished = threading.Event()
            original_llm = LLMClient.complete
            test = self

            class LateArrivalQueue(Queue):
                def get(self, block=True, timeout=None):
                    try:
                        return super().get(block, timeout)
                    except Empty:
                        if threading.current_thread().name == "sticker-images":
                            allow_prompt.set()
                            test.assertTrue(producer_finished.wait(5))
                            for thread in threading.enumerate():
                                if thread.name == "sticker-prompts":
                                    thread.join(5)
                        raise

            def llm(client, prompt, *, refined=False):
                self.assertTrue(allow_prompt.wait(5))
                return original_llm(client, prompt, refined=refined)

            def report(lane, done, name, stage):
                if lane == "prompts" and stage == "done":
                    producer_finished.set()

            with (
                patch("birthday_stickers.engine.Queue", LateArrivalQueue),
                patch.object(LLMClient, "complete", llm),
            ):
                results = run(config, worker_progress=report)
            self.assertEqual(len(results), 1)
            self.assertEqual(len(service.graphs), 2)

    def test_ctrl_c_keeps_session_locked_until_workers_finish_checkpoints(self):
        with tempfile.TemporaryDirectory() as temp, fixtures.FakeServices() as service:
            config = fixtures.PipelineTests().configuration(temp, service)
            store = SessionStore(Path(temp) / "sessions")
            item = store.create(config)
            control = RunControl()
            entered, release, saved_response = (
                threading.Event(),
                threading.Event(),
                threading.Event(),
            )
            observed = []
            original_llm = LLMClient.complete

            def llm(client, prompt, *, refined=False):
                entered.set()
                if not release.wait(5):
                    raise RuntimeError("Interrupt helper did not release the LLM")
                response = original_llm(client, prompt, refined=refined)
                saved_response.set()
                return response

            def interrupt():
                try:
                    if not entered.wait(5):
                        return
                    _thread.interrupt_main()
                    if not control.requested.wait(5):
                        return
                    try:
                        with session_lock(store.directory(item["name"])):
                            observed.append("unlocked too early")
                    except ValueError:
                        observed.append("locked")
                finally:
                    release.set()

            helper = threading.Thread(target=interrupt)
            helper.start()
            try:
                with patch.object(LLMClient, "complete", llm), self.assertRaises(KeyboardInterrupt):
                    store.execute(item["name"], control)
            finally:
                release.set()
                helper.join(6)
            self.assertEqual(observed, ["locked"])
            self.assertTrue(saved_response.is_set())
            records = scene_records(store.directory(item["name"]))
            self.assertIn("raw_scene", records[0][1])
            self.assertEqual(store.read(item["name"])["status"], "stopped")

    def test_session_persists_independent_progress_counters(self):
        with tempfile.TemporaryDirectory() as temp, fixtures.FakeServices() as service:
            config = fixtures.PipelineTests().configuration(temp, service, count=2)
            store = SessionStore(Path(temp) / "sessions")
            item = store.create(config)
            result = store.execute(item["name"], RunControl())
            self.assertEqual(result["prompts_completed"], 2)
            self.assertEqual(result["completed"], 2)
            self.assertEqual(result["prompts_stage"], "done")
            self.assertEqual(result["images_stage"], "done")
            persisted = json.loads(
                (store.directory(item["name"]) / "session.json").read_text(encoding="utf-8")
            )
            self.assertEqual(persisted, result)
