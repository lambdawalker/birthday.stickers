import tempfile
import threading
import unittest
from dataclasses import replace
from unittest.mock import patch

import test_pipeline as fixtures

from birthday_stickers.comfy import ComfyClient
from birthday_stickers.pipeline import run, scene_records
from birthday_stickers.scheduling import gpu_policy


class SchedulingTests(unittest.TestCase):
    def test_ip_policy(self):
        with tempfile.TemporaryDirectory() as temp, fixtures.FakeServices() as service:
            config = fixtures.PipelineTests().configuration(temp, service)
            config.generation.gpu_mode = "parallel"
            for primary, secondary, expected in (
                ("http://10.0.0.1:8188", "http://10.0.0.1:8189", "sequential"),
                ("http://127.0.0.1:8188", "http://[::1]:8189", "sequential"),
                ("http://10.0.0.1:8188", "http://10.0.0.2:8188", "parallel"),
            ):
                config.comfy.base_url = primary
                config.comfy_outpainting = replace(config.comfy, base_url=secondary)
                self.assertEqual(gpu_policy(config)[0], expected)
            config.comfy.base_url = "http://generation:8188"
            config.comfy_outpainting.base_url = "http://outpaint:8188"
            with patch("birthday_stickers.scheduling.host_addresses", return_value={"10.0.0.1"}):
                self.assertEqual(gpu_policy(config)[0], "sequential")
            with patch("birthday_stickers.scheduling.host_addresses", side_effect=OSError):
                self.assertEqual(gpu_policy(config)[0], "sequential")

    def test_parallel_overlap_and_endpoint_routing(self):
        with (
            tempfile.TemporaryDirectory() as temp,
            fixtures.FakeServices() as generation,
            fixtures.FakeServices() as outpaint,
        ):
            config = fixtures.PipelineTests().configuration(temp, generation, count=2)
            config.comfy_outpainting = replace(config.comfy, base_url=outpaint.url)
            config.generation.gpu_mode = "parallel"
            flux_started, second_image = threading.Event(), threading.Event()
            original = ComfyClient.wait_image
            updates = []

            def wait(client, prompt_id, output_id, checkpoint=lambda: None):
                if client.worker == "outpainting" and prompt_id == "job-1":
                    flux_started.set()
                    self.assertTrue(second_image.wait(5), "Generation blocked on outpainting")
                if client.worker == "images" and prompt_id == "job-2":
                    self.assertTrue(flux_started.wait(5))
                    second_image.set()
                return original(client, prompt_id, output_id, checkpoint)

            # Fake HTTP servers share loopback; simulate distinct production IPs here.
            # Actual IP enforcement is tested separately above and below.
            with (
                patch("birthday_stickers.engine.gpu_policy", return_value=("parallel", "test")),
                patch.object(ComfyClient, "wait_image", wait),
            ):
                result = run(config, worker_progress=lambda *args: updates.append(args))
            self.assertEqual(len(result), 2)
            self.assertEqual(len(generation.graphs), 2)
            self.assertEqual(len(outpaint.graphs), 2)
            for lane in ("prompts", "images", "outpainting"):
                self.assertEqual([u[1] for u in updates if u[0] == lane][-1], 2)
            for _, meta in scene_records(config.output.directory):
                self.assertEqual(meta["qwen_server"], generation.url)
                self.assertEqual(meta["flux_server"], outpaint.url)

    def test_same_ip_forces_sequential_even_with_two_ports(self):
        with (
            tempfile.TemporaryDirectory() as temp,
            fixtures.FakeServices() as generation,
            fixtures.FakeServices() as outpaint,
        ):
            config = fixtures.PipelineTests().configuration(temp, generation, count=2)
            config.comfy_outpainting = replace(config.comfy, base_url=outpaint.url)
            config.generation.gpu_mode = "parallel"
            order = []
            original = ComfyClient.wait_image

            def wait(client, *args, **kwargs):
                order.append(client.worker)
                return original(client, *args, **kwargs)

            with patch.object(ComfyClient, "wait_image", wait):
                run(config)
            self.assertEqual(order, ["images", "images", "outpainting", "outpainting"])

    def test_pause_two_inflight_jobs_then_resume_without_resubmitting(self):
        from pathlib import Path

        from birthday_stickers.control import RunControl, RunHalted
        from birthday_stickers.sessions import SessionStore

        with (
            tempfile.TemporaryDirectory() as temp,
            fixtures.FakeServices() as generation,
            fixtures.FakeServices() as outpaint,
        ):
            config = fixtures.PipelineTests().configuration(temp, generation, count=2)
            config.comfy_outpainting = replace(config.comfy, base_url=outpaint.url)
            config.generation.gpu_mode = "parallel"
            store = SessionStore(Path(temp) / "sessions")
            session = store.create(config)
            control = RunControl()
            both = threading.Barrier(2, timeout=5)
            original = ComfyClient.wait_image

            def wait(client, prompt_id, output_id, checkpoint=lambda: None):
                if (
                    client.worker == "images" and prompt_id == "job-2"
                ) or client.worker == "outpainting":
                    both.wait()
                    control.request("paused")
                    raise RunHalted("paused")
                return original(client, prompt_id, output_id, checkpoint)

            with (
                patch("birthday_stickers.engine.gpu_policy", return_value=("parallel", "test")),
                patch.object(ComfyClient, "wait_image", wait),
            ):
                paused = store.execute(session["name"], control)
            self.assertEqual(paused["status"], "paused")
            self.assertEqual(len(generation.graphs), 2)
            self.assertEqual(len(outpaint.graphs), 1)
            with patch("birthday_stickers.engine.gpu_policy", return_value=("parallel", "test")):
                result = store.execute(session["name"], RunControl())
            self.assertEqual(result["status"], "complete", result.get("error"))
            for lane in ("prompts", "images", "outpainting"):
                self.assertEqual(result[f"{lane}_completed"], 2)
            self.assertEqual(len(generation.graphs), 2)
            self.assertEqual(len(outpaint.graphs), 2)
            self.assertEqual(len(generation.chats), 4)

    def test_explicit_sequential_and_concurrent_orders(self):
        for mode, expected in (
            ("sequential", ["images", "images", "outpainting", "outpainting"]),
            ("concurrent", ["images", "outpainting", "images", "outpainting"]),
        ):
            with (
                self.subTest(mode=mode),
                tempfile.TemporaryDirectory() as temp,
                fixtures.FakeServices() as service,
            ):
                config = fixtures.PipelineTests().configuration(temp, service, count=2)
                config.generation.gpu_mode = mode
                order = []
                original = ComfyClient.wait_image

                def wait(client, *args, order=order, original=original, **kwargs):
                    order.append(client.worker)
                    return original(client, *args, **kwargs)

                with patch.object(ComfyClient, "wait_image", wait):
                    run(config)
                self.assertEqual(order, expected)

    def test_sequential_pause_resumes_generated_batch_before_outpainting(self):
        from birthday_stickers.control import RunControl, RunHalted

        with tempfile.TemporaryDirectory() as temp, fixtures.FakeServices() as service:
            config = fixtures.PipelineTests().configuration(temp, service, count=2)
            config.generation.gpu_mode = "sequential"
            control = RunControl()

            def report(lane, done, name, stage):
                if lane == "images" and stage == "image_ready" and done == 1:
                    control.request("paused")

            with self.assertRaises(RunHalted):
                run(config, control=control, worker_progress=report)
            self.assertEqual(len(service.graphs), 1)
            self.assertEqual(len(run(config, resume=True)), 2)
            self.assertEqual(len(service.graphs), 4)
            self.assertIn("461", service.graphs[1])
            self.assertIn("9", service.graphs[2])

    def test_sequential_generation_failure_does_not_start_outpainting(self):
        with tempfile.TemporaryDirectory() as temp, fixtures.FakeServices() as service:
            config = fixtures.PipelineTests().configuration(temp, service, count=2)
            config.generation.gpu_mode = "sequential"
            original = ComfyClient.wait_image

            def wait(client, prompt_id, *args, **kwargs):
                if prompt_id == "job-2":
                    raise RuntimeError("Qwen failed")
                return original(client, prompt_id, *args, **kwargs)

            with patch.object(ComfyClient, "wait_image", wait):
                with self.assertRaisesRegex(RuntimeError, "Qwen failed"):
                    run(config)
            self.assertEqual(len(service.graphs), 2)
            self.assertTrue(all("461" in graph for graph in service.graphs))
