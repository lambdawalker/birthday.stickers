import base64
import io
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import yaml
from PIL import Image

from birthday_stickers.config import load_config
from birthday_stickers.pipeline import run

ROOT = Path(__file__).resolve().parents[1]


class FakeServices:
    """A local HTTP service exercising the real clients, including multipart uploads."""

    def __init__(self):
        self.chats = []
        self.graphs = []
        self.uploads = []
        self.history_gets = 0
        self.fail_refine = False
        self.fail_submit = False
        self.fail_execution = False
        self.never_complete = False
        self.transient_history = False
        self.missing_flux_encoder = None
        self.bad_refinement = False
        service = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def reply(self, payload, status=200, content_type="application/json"):
                data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                data = self.rfile.read(int(self.headers["Content-Length"]))
                if self.path == "/v1/chat/completions":
                    service.chats.append(json.loads(data))
                    if service.fail_refine and len(service.chats) == 2:
                        self.reply({"error": "LLM unavailable"}, 503)
                        return
                    text = (
                        "A child and a creature explore a moonlit castle."
                        if len(service.chats) % 2
                        else "Create a castle scene with the protagonist from <image1> "
                        "and the creature from <image2>, preserving their identities."
                    )
                    if "This is text-to-image" in service.chats[-1]["messages"][0]["content"]:
                        text = "One baby dragon curls up beside a pastel Viking longhouse."
                    if service.bad_refinement and len(service.chats) % 2 == 0:
                        text = (
                            "Create a castle scene with both characters but missing reference tags."
                        )
                    self.reply(
                        {"choices": [{"message": {"content": text}, "finish_reason": "stop"}]}
                    )
                elif self.path == "/upload/image":
                    service.uploads.append(data)
                    self.reply(
                        {
                            "name": f"upload-{len(service.uploads)}.png",
                            "subfolder": "refs",
                            "type": "input",
                        }
                    )
                elif self.path == "/prompt":
                    service.graphs.append(json.loads(data)["prompt"])
                    if service.fail_submit:
                        self.reply({"error": "submission response lost"}, 503)
                    else:
                        self.reply({"prompt_id": f"job-{len(service.graphs)}", "node_errors": {}})
                else:
                    self.reply({"error": self.path}, 404)

            def do_GET(self):
                parts = urlsplit(self.path)
                if parts.path == "/object_info":
                    graphs = [
                        json.loads((ROOT / f"workflows/{name}.json").read_text(encoding="utf-8"))
                        for name in ("qwen-edit", "flux-outpaint")
                    ]
                    info = {
                        node["class_type"]: {"input": {"required": {}}}
                        for graph in graphs
                        for node in graph.values()
                    }
                    if service.missing_flux_encoder:
                        info["DualCLIPLoader"]["input"]["required"][
                            service.missing_flux_encoder
                        ] = [["a-different-encoder.safetensors"], {}]
                    self.reply(info)
                elif parts.path.startswith("/history/"):
                    service.history_gets += 1
                    if service.transient_history and service.history_gets == 1:
                        self.reply({"error": "temporary"}, 503)
                        return
                    job_id = parts.path.split("/")[-1]
                    if service.never_complete or service.history_gets == 1:
                        self.reply({})
                        return
                    if service.fail_execution:
                        self.reply(
                            {
                                job_id: {
                                    "status": {
                                        "status_str": "error",
                                        "messages": [
                                            [
                                                "execution_error",
                                                {"node_id": "3", "exception_message": "GPU OOM"},
                                            ]
                                        ],
                                    }
                                }
                            }
                        )
                        return
                    graph = service.graphs[int(job_id.split("-")[-1]) - 1]
                    output = "461" if "461" in graph else "9"
                    self.reply(
                        {
                            job_id: {
                                "status": {"completed": True, "status_str": "success"},
                                "outputs": {
                                    "999": {"images": [{"filename": "wrong.png"}]},
                                    output: {
                                        "images": [
                                            {
                                                "filename": job_id + ".png",
                                                "subfolder": "",
                                                "type": "output",
                                            }
                                        ]
                                    },
                                },
                            }
                        }
                    )
                elif parts.path == "/view":
                    filename = parse_qs(parts.query)["filename"][0]
                    number = int(filename.removeprefix("job-").removesuffix(".png"))
                    graph = service.graphs[number - 1]
                    if "461" in graph:
                        size, color = (32, 32), "red"
                    else:
                        pad = graph["44"]["inputs"]
                        size = (32 + pad["left"] + pad["right"], 32 + pad["top"] + pad["bottom"])
                        color = "blue"
                    buffer = io.BytesIO()
                    Image.new("RGB", size, color).save(buffer, format="PNG")
                    self.reply(buffer.getvalue(), content_type="image/png")
                else:
                    self.reply({"error": self.path}, 404)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server.server_port}"


class PipelineTests(unittest.TestCase):
    def configuration(self, temp, services, count=1):
        directory = Path(temp)
        data = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
        data["prompts"].pop("activities", None)
        data["characters"] = {}  # Exercise legacy explicit-path configurations too.
        for key in ("protagonist", "creature"):
            path = directory / (key + ".png")
            Image.new("RGB", (32, 32), "green").save(path)
            data["characters"][key] = str(path)
        for key, text in {
            "planning": "Plan one scene. {{iteration}} of {{count}}.",
            "guide": "GUIDE MARKER: keep identities.",
            "refinement": "Use this guide: {{guide}}\nScene: {{scene}}",
        }.items():
            path = directory / (key + ".md")
            path.write_text(text, encoding="utf-8")
            data["prompts"][key] = str(path)
        for item in data["workflows"].values():
            item["path"] = str(ROOT / item["path"])
        data["llm"].update(base_url=services.url + "/v1", model="fixture")
        data["comfy"].update(
            base_url=services.url, poll_interval=0.002, retry_delay=0, job_timeout=2
        )
        data["generation"].update(count=count, width=32, height=32, seed=100)
        data["output"].update(
            directory=str(directory / "output"),
            width=53,
            height=55,
            feathering=4,
            outpainting_prompt="",
        )
        path = directory / "config.yaml"
        path.write_text(yaml.safe_dump(data), encoding="utf-8")
        return load_config(path)

    def test_two_full_cycles_use_correct_prompts_images_and_sizes_and_append(self):
        with tempfile.TemporaryDirectory() as temp, FakeServices() as service:
            config = self.configuration(temp, service, count=2)
            run(config)
            self.assertEqual(len(service.chats), 4)
            self.assertEqual(len(service.graphs), 4)
            self.assertEqual(len(service.uploads), 4)  # 2 refs once, then 2 intermediates
            refined_request = service.chats[1]["messages"][-1]["content"]
            self.assertIn("GUIDE MARKER", refined_request)
            self.assertIn("moonlit castle", refined_request)
            self.assertEqual(service.graphs[0]["514"]["inputs"]["image"], "refs/upload-1.png")
            self.assertEqual(service.graphs[0]["516"]["inputs"]["image"], "refs/upload-2.png")
            self.assertEqual(service.graphs[1]["47"]["inputs"]["image"], "refs/upload-3.png")
            self.assertIn("<image1>", service.graphs[0]["459:474"]["inputs"]["prompt"])
            self.assertEqual(
                service.graphs[1]["23"]["inputs"]["text"],
                "A child and a creature explore a moonlit castle.",
            )
            self.assertEqual(service.graphs[0]["459:458"]["inputs"]["seed"], 100)
            self.assertEqual(service.graphs[1]["3"]["inputs"]["seed"], 101)
            root = config.output.directory
            for index in range(2):
                name = f"stiker-{index:04d}"
                with Image.open(root / "final" / (name + ".jpg")) as image:
                    self.assertEqual(image.size, (53, 55))
                    self.assertEqual(image.format, "JPEG")
                    self.assertGreater(image.getpixel((26, 27))[0], 240)
                text = (root / "prompts" / (name + ".md")).read_text(encoding="utf-8")
                self.assertIn("## Raw scene", text)
                self.assertIn("## Refined scene", text)
                self.assertIn("<image1>", text)
                meta = json.loads(
                    (root / "metadata" / (name + ".json")).read_text(encoding="utf-8")
                )
                self.assertEqual(meta["status"], "complete")
                self.assertIn("qwen_prompt_id", meta)
                self.assertIn("flux_prompt_id", meta)
            config.generation.count = 1
            run(config)
            self.assertTrue((root / "final/stiker-0002.jpg").exists())

    def test_activity_brief_reaches_llm_and_persists_across_failed_runs(self):
        with tempfile.TemporaryDirectory() as temp, FakeServices() as service:
            config = self.configuration(temp, service)
            data = yaml.safe_load(config.path.read_text(encoding="utf-8"))
            data["prompts"]["activities"] = str(ROOT / "prompts/activities.json")
            data["prompts"]["planning"] = str(ROOT / "prompts/plan_scene.md")
            config.path.write_text(yaml.safe_dump(data), encoding="utf-8")
            config = load_config(config.path)
            service.fail_refine = True
            with self.assertRaisesRegex(RuntimeError, "stiker-0000"):
                run(config)
            root = config.output.directory
            first = json.loads((root / "metadata/stiker-0000.json").read_text(encoding="utf-8"))
            self.assertEqual(first["status"], "failed")
            service.fail_refine = False
            run(config)
            second = json.loads((root / "metadata/stiker-0001.json").read_text(encoding="utf-8"))
            self.assertNotEqual(first["activity"]["id"], second["activity"]["id"])
            for index, meta in enumerate((first, second)):
                request = service.chats[index * 2]["messages"][-1]["content"]
                self.assertIn(meta["activity"]["activity"], request)
                self.assertIn(meta["activity"]["setting"], request)
                self.assertIn("Viking", request)
                self.assertIn("blush pink", request)
                markdown = (root / "prompts" / f"stiker-{index:04d}.md").read_text(encoding="utf-8")
                self.assertIn(meta["activity"]["id"], markdown)

    def test_refinement_failure_preserves_raw_scene(self):
        with tempfile.TemporaryDirectory() as temp, FakeServices() as service:
            service.fail_refine = True
            config = self.configuration(temp, service)
            with self.assertRaisesRegex(RuntimeError, "stiker-0000"):
                run(config)
            text = (config.output.directory / "prompts/stiker-0000.md").read_text(encoding="utf-8")
            self.assertIn("moonlit castle", text)
            self.assertEqual(len(service.graphs), 0)

    def test_submission_is_not_retried_and_metadata_survives(self):
        with tempfile.TemporaryDirectory() as temp, FakeServices() as service:
            service.fail_submit = True
            config = self.configuration(temp, service)
            with self.assertRaisesRegex(RuntimeError, "not retried"):
                run(config)
            self.assertEqual(len(service.graphs), 1)
            self.assertTrue((config.output.directory / "metadata/stiker-0000.json").exists())

    def test_get_retry_recovers_without_resubmitting(self):
        with tempfile.TemporaryDirectory() as temp, FakeServices() as service:
            service.transient_history = True
            run(self.configuration(temp, service))
            self.assertEqual(len(service.graphs), 2)
            self.assertGreaterEqual(service.history_gets, 3)

    def test_execution_error_reports_node_and_persists_prompt_id(self):
        with tempfile.TemporaryDirectory() as temp, FakeServices() as service:
            service.fail_execution = True
            config = self.configuration(temp, service)
            with self.assertRaisesRegex(RuntimeError, "GPU OOM"):
                run(config)
            meta = json.loads(
                (config.output.directory / "metadata/stiker-0000.json").read_text(encoding="utf-8")
            )
            self.assertEqual(meta["qwen_prompt_id"], "job-1")

    def test_timeout_is_bounded_and_does_not_resubmit(self):
        with tempfile.TemporaryDirectory() as temp, FakeServices() as service:
            service.never_complete = True
            config = self.configuration(temp, service)
            config.comfy.job_timeout = 0.04
            with self.assertRaisesRegex(RuntimeError, "timed out.*job-1"):
                run(config)
            self.assertEqual(len(service.graphs), 1)

    def test_equal_target_skips_outpaint(self):
        with tempfile.TemporaryDirectory() as temp, FakeServices() as service:
            config = self.configuration(temp, service)
            config.output.width = config.output.height = 32
            run(config)
            self.assertEqual(len(service.graphs), 1)

    def test_missing_numbered_flux_encoder_fails_before_llm_or_rendering(self):
        with tempfile.TemporaryDirectory() as temp, FakeServices() as service:
            config = self.configuration(temp, service)
            for field in ("clip_name1", "clip_name2"):
                service.missing_flux_encoder = field
                with self.subTest(field=field), self.assertRaisesRegex(RuntimeError, field):
                    run(config)
            self.assertEqual(service.chats, [])
            self.assertEqual(service.graphs, [])

    def test_invalid_refinement_is_saved_for_inspection(self):
        with tempfile.TemporaryDirectory() as temp, FakeServices() as service:
            service.bad_refinement = True
            config = self.configuration(temp, service)
            with self.assertRaisesRegex(RuntimeError, "must name both"):
                run(config)
            saved = (config.output.directory / "prompts/stiker-0000.md").read_text(encoding="utf-8")
            self.assertIn("## Refined scene", saved)
            self.assertIn("missing reference tags", saved)
            self.assertEqual(service.graphs, [])

    def test_random_pair_is_selected_per_scene_and_shared_with_vision_and_qwen(self):
        with tempfile.TemporaryDirectory() as temp, FakeServices() as service:
            config = self.configuration(temp, service, count=2)
            directory = Path(temp) / "characters"
            directory.mkdir()
            for name, color in (
                ("A000.jpg", "red"),
                ("A001.jpg", "green"),
                ("B000.jpg", "blue"),
                ("B001.jpg", "yellow"),
            ):
                Image.new("RGB", (32, 32), color).save(directory / name)
            data = yaml.safe_load(config.path.read_text(encoding="utf-8"))
            data["characters"] = {"directory": "characters", "protagonist": "A", "creature": "B"}
            data["llm"]["send_reference_images"] = True
            config.path.write_text(yaml.safe_dump(data), encoding="utf-8")
            config = load_config(config.path)
            chosen = [directory / name for name in ("A000.jpg", "B001.jpg", "A001.jpg", "B000.jpg")]
            with patch("birthday_stickers.engine.secrets.choice", side_effect=chosen):
                run(config)
            for index in range(2):
                meta = json.loads(
                    (config.output.directory / "metadata" / f"stiker-{index:04d}.json").read_text(
                        encoding="utf-8"
                    )
                )
                self.assertEqual(
                    [Path(ref["path"]).name for ref in meta["references"]],
                    [path.name for path in chosen[2 * index : 2 * index + 2]],
                )
                graph = service.graphs[index * 2]
                self.assertEqual(
                    graph["514"]["inputs"]["image"], f"refs/upload-{1 + 3 * index}.png"
                )
                self.assertEqual(
                    graph["516"]["inputs"]["image"], f"refs/upload-{2 + 3 * index}.png"
                )
                for request in service.chats[index * 2 : index * 2 + 2]:
                    parts = request["messages"][-1]["content"]
                    for slot in range(2):
                        encoded = parts[slot + 1]["image_url"]["url"].split(",", 1)[1]
                        with Image.open(io.BytesIO(base64.b64decode(encoded))) as preview:
                            with Image.open(chosen[index * 2 + slot]) as original:
                                self.assertEqual(
                                    preview.getpixel((16, 16)), original.getpixel((16, 16))
                                )

    def test_missing_character_variants_fail_before_any_network_work(self):
        with tempfile.TemporaryDirectory() as temp, FakeServices() as service:
            config = self.configuration(temp, service)
            config.character_directory = Path(temp)
            config.protagonist, config.creature = "A", "B"
            Image.new("RGB", (32, 32)).save(Path(temp) / "A000.jpg")
            with self.assertRaisesRegex(ValueError, "No references for creature character B"):
                run(config)
            self.assertEqual(service.chats, [])
            self.assertEqual(service.graphs, [])
