"""Textual session picker and responsive generation dashboard."""

import copy
import logging
import math

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    ProgressBar,
    RichLog,
    Static,
    Switch,
)

from .config import Config, load_config
from .control import RunControl
from .flows import get_flow
from .presets import discover_presets, with_preset
from .references import discover_characters
from .retry import retry_scope
from .sessions import SessionStore, service_urls
from .workflows import load_workflow


class SessionUpdate(Message):
    def __init__(self, value: dict):
        super().__init__()
        self.value = value


class RunFinished(Message):
    def __init__(self, error: str = ""):
        super().__init__()
        self.error = error


class LogLine(Message):
    def __init__(self, text: str, worker: str = "system"):
        super().__init__()
        self.text = text
        self.worker = worker


class UILogHandler(logging.Handler):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.setFormatter(logging.Formatter("%(asctime)s  %(message)s", datefmt="%H:%M:%S"))

    def emit(self, record):
        worker = getattr(record, "worker", "images" if record.name.endswith("comfy") else "system")
        self.app.post_message(LogLine(self.format(record), worker))


class StickerApp(App):
    TITLE = "Birthday Stickers"
    SUB_TITLE = "Viking storybook studio"
    BINDINGS = [("q", "quit_safely", "Quit"), ("ctrl+c", "quit_safely", "Save & quit")]
    CSS = """
    Screen { background: #101522; color: #e7e4f2; }
    Header { background: #24243d; color: #e5c9fa; }
    Footer { background: #24243d; }
    #type-picker, #home, #dashboard { padding: 1 3; }
    .title { color: #e5c9fa; text-style: bold; margin-bottom: 0; }
    .muted { color: #acb4ca; margin-bottom: 0; }
    .card { border: round #565277; padding: 0 1; height: auto; margin-bottom: 1; }
    .fields { height: auto; }
    #reference-settings { height: auto; margin: 0; border: none; padding: 0; }
    .field { width: 1fr; height: auto; margin-right: 2; }
    Input { background: #1b2233; border: tall #565277; }
    Input:focus { border: tall #b7a1e8; }
    .toggle-row { height: 3; align-vertical: middle; }
    .toggle-row Label { width: auto; margin: 1 2 0 1; }
    #outpaint-size { height: auto; color: #acb4ca; }
    .session-actions Label { margin: 2 1 0 1; }
    .session-actions Switch { margin-top: 1; }
    .buttons { height: auto; margin-top: 0; }
    Button { margin: 1 1; padding: 0 2; min-width: 16; }
    Button.-primary { background: #b7a1e8; color: #101522; }
    Button.-success { background: #8dd5c8; color: #101522; }
    Button.-warning { background: #f5dba7; color: #101522; }
    #sessions { height: 6; background: #1b2233; }
    #session-title { color: #e5c9fa; text-style: bold; }
    #stage { margin: 1 0; color: #a9d9ed; }
    #workers { height: 1fr; min-height: 16; }
    .worker { width: 1fr; height: 100%; border: round #565277; padding: 0 1; }
    #prompt-worker, #image-worker { margin-right: 1; }
    .worker-url { height: auto; min-height: 1; color: #acb4ca; }
    .worker-stage { height: 3; color: #a9d9ed; }
    .worker-count { height: 1; color: #acb4ca; }
    ProgressBar { width: 100%; margin: 1 0; }
    RichLog { height: 1fr; min-height: 5; background: #151c2c; }
    #output, #hint { color: #acb4ca; height: auto; }
    #error { color: #ffb4bc; height: auto; margin: 0; }
    #home, #dashboard { display: none; }
    #type-error { color: #ffb4bc; height: auto; }
    #dashboard.compact #hint, #dashboard.compact #output { display: none; }
    #dashboard.compact #stage { margin: 0; }
    #dashboard.compact ProgressBar { margin: 0; }
    #dashboard.compact .worker-stage { height: 2; }
    #dashboard.compact #workers { min-height: 12; }
    """

    def __init__(self, config: Config, store: SessionStore | None = None):
        super().__init__()
        self.config = config
        self.launch_config = copy.deepcopy(config)
        self.store = store or SessionStore(config.output.directory / "sessions")
        self.control = RunControl()
        self.busy = False
        self.active: dict | None = None
        self.return_home = False
        self.quitting = False
        self.handler = UILogHandler(self)
        self.preset_choices = discover_presets(config.path)
        if config.preset:
            self.preset_choices[config.preset.directory.name] = config.preset.directory
        self.preset_buttons = {
            f"choose-preset-{index}": path
            for index, path in enumerate(self.preset_choices.values())
        }

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll(id="type-picker"):
            yield Static("What would you like to create?", classes="title")
            yield Static("Choose a scene preset to start or resume a collection.", classes="muted")
            for button_id, path in self.preset_buttons.items():
                with Vertical(classes="card"):
                    yield Label(path.name.replace("_", " ").title(), classes="title")
                    yield Static(str(path), classes="muted")
                    yield Button("Select preset", variant="primary", id=button_id)
            yield Static("", id="type-error")
        with VerticalScroll(id="home"):
            yield Static("A new adventure, or right where you left off.", classes="title")
            yield Static(
                "Create a collection, or update and resume a saved session.",
                classes="muted",
            )
            with Vertical(classes="card"):
                yield Label(
                    f"NEW SESSION · {self.config.generation.type}", classes="title", id="new-type"
                )
                with Horizontal(classes="fields"):
                    with Vertical(classes="field"):
                        yield Label("Session name · automatic")
                        yield Input(self.store.next_name(), disabled=True, id="new-name")
                    with Vertical(classes="field"):
                        yield Label("Number of stickers")
                        yield Input(str(self.config.generation.count), type="integer", id="count")
                with Vertical(id="reference-settings", classes="card"):
                    yield Static("", id="reference-counts")
                    with Horizontal(classes="fields"):
                        for key, title in (
                            ("cfg", "CFG"),
                            ("width", "Width (px)"),
                            ("height", "Height (px)"),
                        ):
                            with Vertical(classes="field"):
                                yield Label(title)
                                yield Input(
                                    id=f"generation-{key}",
                                    type="number" if key == "cfg" else "integer",
                                )
                with Horizontal(classes="buttons session-actions"):
                    yield Button("New session", variant="primary", id="new")
                    yield Label("Outpainting", id="outpaint-label")
                    yield Switch(self.config.generation.outpainting, id="outpainting")
                yield Static("", id="outpaint-size")
                yield Button("Change preset", id="change-type")
                yield Static(
                    "Prompts/settings are saved; new scenes use live character references.",
                    classes="muted",
                )
            yield Label("SAVED SESSIONS", classes="title")
            yield DataTable(id="sessions", cursor_type="row")
            yield Static("", id="empty", classes="muted")
            yield Static("", id="saved-generation-settings", classes="muted")
            with Horizontal(classes="buttons session-actions"):
                yield Label("Outpainting")
                yield Switch(False, id="session-outpainting", disabled=True)
                yield Button("Resume selected", variant="success", id="open")
                yield Button("Refresh", id="refresh")
        with VerticalScroll(id="dashboard"):
            with Vertical(classes="card"):
                yield Static("", id="session-title")
                yield Static("Preparing session…", id="stage")
                yield Static("", id="totals")
                yield Static("", id="output")
                yield Static("", id="error")
                with Horizontal(classes="buttons"):
                    yield Button("Pause", variant="warning", id="pause")
                    yield Button("Resume", variant="success", id="resume")
                    yield Button("Stop", id="stop")
                    yield Button("Sessions", id="back")
                yield Static(
                    "Pause keeps this screen. Stop returns to sessions. Both save progress.",
                    id="hint",
                )
            with Horizontal(id="workers"):
                for lane, panel, title in (
                    ("prompts", "prompt", "SCENE PLANNING"),
                    ("images", "image", "IMAGE GENERATION"),
                    ("outpainting", "outpainting", "OUTPAINTING"),
                ):
                    with Vertical(id=f"{panel}-worker", classes="worker"):
                        yield Label(title, classes="title")
                        yield Static("Loading endpoint…", id=f"{lane}-url", classes="worker-url")
                        yield Static("Waiting to start", id=f"{lane}-stage", classes="worker-stage")
                        if lane == "images":
                            yield Static("", id="selected-references")
                        yield ProgressBar(total=100, show_eta=False, id=f"{lane}-progress")
                        yield Static("", id=f"{lane}-count", classes="worker-count")
                        yield RichLog(
                            id=f"{lane}-logs", wrap=True, min_width=1, markup=False, max_lines=1000
                        )
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#sessions", DataTable).add_columns(
            "Session", "Type", "Outpaint", "Progress", "Status", "Last step"
        )
        self.refresh_sessions()
        self.update_reference_settings()
        self.update_outpaint_summary()
        self.query_one("#dashboard").set_class(self.size.height < 32, "compact")
        logger = logging.getLogger("birthday_stickers")
        self.old_log_level = logger.level
        logger.setLevel(logging.INFO)
        logger.addHandler(self.handler)

    def on_resize(self) -> None:
        if self.is_mounted:
            self.query_one("#dashboard").set_class(self.size.height < 32, "compact")

    def on_unmount(self) -> None:
        logger = logging.getLogger("birthday_stickers")
        logger.removeHandler(self.handler)
        logger.setLevel(self.old_log_level)

    def refresh_sessions(self) -> None:
        table = self.query_one("#sessions", DataTable)
        table.clear()
        items = self.store.list_sessions()
        for item in items:
            status = item["status"]
            if status == "running":
                status = "Running / interrupted"
            table.add_row(
                item["name"],
                item.get("preset_name") or item.get("generation_type", "multi_character_reference"),
                "On"
                if item.get(
                    "outpainting",
                    get_flow(
                        item.get("generation_type", "multi_character_reference")
                    ).uses_references,
                )
                else "Off",
                f"{item.get('prompts_completed', item.get('completed', 0))} prompts / "
                f"{item.get('completed', 0)} images / {item['count']}",
                status,
                item.get("stage", ""),
                key=item["name"],
            )
        self.query_one("#open", Button).disabled = not items
        self.query_one("#session-outpainting", Switch).disabled = not items
        if items:
            self.sync_session_toggle(items[0]["name"])
        text = (
            "Select a session, choose Outpainting, then press Resume selected."
            if items
            else ("No saved sessions yet. Start your first collection above.")
        )
        if self.store.errors:
            text += "\nUnreadable sessions: " + "; ".join(self.store.errors)
        self.query_one("#empty", Static).update(Text(text))
        self.query_one("#new-name", Input).value = self.store.next_name()

    def sync_session_toggle(self, name: str) -> None:
        try:
            saved = load_config(self.store.directory(name) / "config.yaml")
            self.query_one("#session-outpainting", Switch).value = saved.generation.outpainting
            cfg = saved.generation.cfg
            if cfg is None and saved.flow.uses_references:
                sampler = str(saved.qwen.nodes["sampler"])
                cfg = saved.qwen.overrides.get(sampler, {}).get(
                    "cfg", load_workflow(saved.qwen.path)[sampler]["inputs"]["cfg"]
                )
            self.query_one("#saved-generation-settings", Static).update(
                f"Selected session · Qwen {saved.generation.width} × {saved.generation.height}"
                + (f" · CFG {cfg:g}" if cfg is not None else "")
                + " · saved settings"
            )
        except (OSError, ValueError) as error:
            self.notify(str(error), severity="error")

    @on(DataTable.RowHighlighted, "#sessions")
    def highlighted_session(self, event: DataTable.RowHighlighted) -> None:
        if event.row_key.value is not None:
            self.sync_session_toggle(str(event.row_key.value))

    def resume_selected(self, name: str) -> None:
        if self.busy:
            return
        try:
            enabled = self.query_one("#session-outpainting", Switch).value
            self.store.set_outpainting(name, enabled, self.config)
        except (OSError, ValueError, RuntimeError) as error:
            self.notify(f"Cannot update session: {error}", severity="error")
            return
        self.begin(name)

    def choose_preset(self, directory) -> None:
        if self.busy:
            return
        try:
            self.config = with_preset(self.launch_config, directory)
        except (OSError, ValueError) as error:
            self.query_one("#type-error", Static).update(Text(str(error)))
            return
        self.update_reference_settings()
        self.query_one("#outpainting", Switch).value = self.config.generation.outpainting
        self.update_outpaint_summary()
        self.query_one("#new-type", Label).update(
            "NEW SESSION · " + self.config.preset.directory.name
        )
        self.query_one("#count", Input).value = str(self.config.generation.count)
        self.query_one("#type-error", Static).update("")
        self.show_home()
        self.query_one("#new", Button).focus()

    def update_reference_settings(self) -> None:
        self.query_one("#reference-settings").display = (
            bool(self.config.preset) or self.config.flow.uses_references
        )
        if not self.config.preset and not self.config.flow.uses_references:
            return
        try:
            if self.config.preset:
                import re

                directory = self.config.character_directory
                files = list(directory.iterdir()) if directory and directory.is_dir() else []
                counts = []
                for character in self.config.preset.characters:
                    prefix = character.get("imagePrefix")
                    if prefix:
                        pattern = re.compile(
                            re.escape(prefix) + r"[0-9]+\.(?i:jpg|jpeg|png|webp|bmp|tiff|tif)"
                        )
                        matches = [p for p in files if p.is_file() and pattern.fullmatch(p.name)]
                        count = sum(p.suffix.lower() == ".jpg" for p in matches)
                        originals = len(matches) - count
                        counts.append(
                            f"{character['name']} ({character['id']}): {count} JPG"
                            + (f" + {originals} source files" if originals else "")
                        )
                    else:
                        counts.append(f"{character['name']} ({character['id']}): text")
                text = "Character sources · " + " · ".join(counts)
            elif self.config.character_directory:
                groups = discover_characters(self.config.character_directory)
                text = "Character versions · " + " · ".join(
                    f"{label}: {len(paths)}" for label, paths in sorted(groups.items())
                )
            else:
                text = "Character versions · 1 explicit reference per role"
            self.query_one("#reference-counts", Static).update(Text(text))
            cfg = self.config.generation.cfg
            if cfg is None:
                sampler = str(self.config.qwen.nodes["sampler"])
                cfg = self.config.qwen.overrides.get(sampler, {}).get(
                    "cfg", load_workflow(self.config.qwen.path)[sampler]["inputs"]["cfg"]
                )
            self.query_one("#generation-cfg", Input).value = f"{cfg:g}"
            for key in ("width", "height"):
                self.query_one(f"#generation-{key}", Input).value = str(
                    getattr(self.config.generation, key)
                )
        except (OSError, ValueError, KeyError) as error:
            self.query_one("#reference-counts", Static).update(Text(str(error)))

    def apply_generation_settings(self) -> None:
        if not self.config.preset and not self.config.flow.uses_references:
            return
        cfg = float(self.query_one("#generation-cfg", Input).value)
        width = int(self.query_one("#generation-width", Input).value)
        height = int(self.query_one("#generation-height", Input).value)
        if not math.isfinite(cfg) or cfg < 0:
            raise ValueError("CFG must be a finite nonnegative number.")
        if any(value < 32 or value > 16384 or value % 32 for value in (width, height)):
            raise ValueError("Width and height must be multiples of 32 between 32 and 16384.")
        if self.query_one("#outpainting", Switch).value and (
            width > self.config.output.width or height > self.config.output.height
        ):
            raise ValueError("Generation resolution cannot exceed the outpainting target.")
        self.config.generation.cfg = cfg
        self.config.generation.width = width
        self.config.generation.height = height

    def update_outpaint_summary(self) -> None:
        enabled = self.query_one("#outpainting", Switch).value
        size = self.config.output if enabled else self.config.generation
        self.query_one("#outpaint-size", Static).update(
            f"New sessions: {'extend background' if enabled else 'Qwen only'} · "
            f"final {size.width} × {size.height}."
        )

    @on(Switch.Changed, "#outpainting")
    def outpainting_changed(self, event: Switch.Changed) -> None:
        self.update_outpaint_summary()

    def show_dashboard(self) -> None:
        self.query_one("#type-picker").display = False
        self.query_one("#home").display = False
        self.query_one("#dashboard").display = True
        for lane in ("prompts", "images", "outpainting"):
            self.query_one(f"#{lane}-logs", RichLog).clear()
        self.query_one("#hint", Static).update(
            "Pause keeps this screen. Stop returns to sessions. Both save progress."
        )
        self.query_one("#error", Static).update("")
        self.query_one("#error").display = False
        self.query_one("#pause", Button).disabled = False
        self.query_one("#resume", Button).disabled = True
        self.query_one("#stop", Button).disabled = False
        self.query_one("#back", Button).disabled = True

    def show_home(self) -> None:
        self.query_one("#type-picker").display = False
        self.query_one("#dashboard").display = False
        self.query_one("#home").display = True
        self.refresh_sessions()

    def begin(self, name: str | None = None, count: int | None = None) -> None:
        if self.busy:
            return
        self.busy = True
        self.return_home = False
        self.control = RunControl()
        self.show_dashboard()
        self.query_one("#selected-references", Static).update("")
        self.query_one("#session-title", Static).update(name or self.store.next_name())
        self.query_one("#stage", Static).update("Preparing session…")
        self.query_one("#totals", Static).update("")
        self.query_one("#output", Static).update("")
        for lane in ("prompts", "images", "outpainting"):
            self.query_one(f"#{lane}-progress", ProgressBar).update(total=count or 100, progress=0)
            self.query_one(f"#{lane}-stage", Static).update("Waiting to start")
            self.query_one(f"#{lane}-url", Static).update("Loading endpoint…")
            self.query_one(f"#{lane}-count", Static).update("")
        self.execute_session(name, count)

    @work(thread=True, exit_on_error=False)
    def execute_session(self, name: str | None, count: int | None) -> None:
        with retry_scope(self.control.check):
            self.execute_session_with_retries(name, count)

    def execute_session_with_retries(self, name, count):
        try:
            if name is None:
                item = self.store.create(self.config, count)
                name = item["name"]
            item = self.store.read(name)
            saved_config = load_config(self.store.directory(name) / "config.yaml")
            item["service_urls"] = service_urls(saved_config)
            self.post_message(SessionUpdate(item))
            item = self.store.execute(
                name, self.control, lambda value: self.post_message(SessionUpdate(value))
            )
            self.post_message(SessionUpdate(item))
            self.post_message(RunFinished())
        except Exception as error:
            self.post_message(RunFinished(str(error)))

    def on_session_update(self, event: SessionUpdate) -> None:
        self.active = event.value
        item = self.active
        self.query_one("#selected-references", Static).update(
            Text("References: " + " · ".join(item.get("images_references", [])))
            if item.get("images_references")
            else ""
        )
        for lane in ("prompts", "images", "outpainting"):
            url = item.get("service_urls", {}).get(lane, "Loading endpoint…")
            if lane == "outpainting" and not item.get("outpainting", True):
                url += " (disabled)"
            widget = self.query_one(f"#{lane}-url", Static)
            widget.update(Text(url))
            widget.tooltip = url
        self.query_one("#session-title", Static).update(
            Text(
                item["name"]
                + " · "
                + (
                    item.get("preset_name")
                    or item.get("generation_type", "multi_character_reference")
                )
            )
        )
        status = item["status"].upper()
        if self.busy and self.control.requested.is_set():
            status = "SAVING ALL WORKERS"
        self.query_one("#stage", Static).update(Text(status))
        prompt_count = item.get("prompts_completed", item.get("completed", 0))
        image_count = item.get("images_completed", item.get("completed", 0))
        outpainting_count = item.get("outpainting_completed", 0)
        stages = {
            "planning": "Planning scene",
            "refining": "Refining Qwen prompt",
            "prompts_ready": "Prompt saved to queue",
            "done": "Finished",
            "waiting_for_prompts": "Waiting for a ready prompt",
            "waiting_for_generation_batch": "Waiting for all images to finish",
            "upload_references": "Uploading character references",
            "rendering_qwen": "Rendering with Qwen",
            "save_intermediate": "Saving intermediate JPEG",
            "upload_intermediate": "Uploading intermediate image",
            "rendering_flux": "Outpainting with Flux",
            "save_final": "Saving final JPEG",
            "complete": "Image saved",
        }
        for lane, done, label in (
            ("prompts", prompt_count, "prompts ready"),
            ("images", image_count, "images saved"),
            ("outpainting", outpainting_count, "outpainted"),
        ):
            self.query_one(f"#{lane}-progress", ProgressBar).update(
                total=item["count"], progress=done
            )
            self.query_one(f"#{lane}-count", Static).update(f"{done} / {item['count']} {label}")
            stage = item.get(f"{lane}_stage", "Ready")
            sticker = item.get(f"{lane}_sticker", "")
            self.query_one(f"#{lane}-stage", Static).update(
                Text(f"{sticker}\n{stages.get(stage, stage.replace('_', ' ').capitalize())}")
            )
        self.query_one("#totals", Static).update(
            f"{prompt_count} prompts ready · {image_count} images saved · "
            f"{item.get('completed', 0)} final · "
            f"GPU: {item.get('effective_gpu_mode', item.get('gpu_mode', 'sequential'))}"
        )
        stage_folder = "outpainting" if item.get("outpainting") else "image_generation"
        output = self.store.directory(item["name"]) / stage_folder / "results"
        self.query_one("#output", Static).update(Text(f"Results: {output}"))
        self.query_one("#error", Static).update(Text(item.get("error", "")))
        self.query_one("#error").display = bool(item.get("error"))

    def on_run_finished(self, event: RunFinished) -> None:
        self.busy = False
        self.query_one("#pause", Button).disabled = True
        self.query_one("#back", Button).disabled = False
        self.query_one("#resume", Button).disabled = (
            self.active is None or self.active.get("status") == "complete"
        )
        if event.error:
            self.query_one("#error", Static).update(Text(event.error))
            self.query_one("#error").display = True
            self.query_one("#stage", Static).update("Unable to start session")
        if self.quitting:
            self.exit()
        elif self.return_home:
            self.show_home()

    def on_log_line(self, event: LogLine) -> None:
        lanes = (
            (event.worker,)
            if event.worker in ("prompts", "images", "outpainting")
            else ("prompts", "images", "outpainting")
        )
        for lane in lanes:
            self.query_one(f"#{lane}-logs", RichLog).write(Text(event.text))

    @on(DataTable.RowSelected, "#sessions")
    def selected(self, event: DataTable.RowSelected) -> None:
        self.sync_session_toggle(str(event.row_key.value))

    @on(Button.Pressed)
    def pressed(self, event: Button.Pressed) -> None:
        action = event.button.id
        if action in self.preset_buttons:
            self.choose_preset(self.preset_buttons[action])
        elif action == "change-type" and not self.busy:
            self.query_one("#home").display = False
            self.query_one("#type-picker").display = True
            self.query_one("#" + next(iter(self.preset_buttons)), Button).focus()
        elif action == "new":
            try:
                count = int(self.query_one("#count", Input).value)
                if count < 1:
                    raise ValueError()
            except ValueError:
                self.notify("Enter a positive number of stickers.", severity="error")
                return
            try:
                self.apply_generation_settings()
            except ValueError as error:
                self.notify(
                    str(error) or "Enter valid CFG and resolution values.", severity="error"
                )
                return
            self.config.generation.outpainting = self.query_one("#outpainting", Switch).value
            self.active = None
            self.begin(count=count)
        elif action == "open":
            table = self.query_one("#sessions", DataTable)
            if table.row_count:
                self.resume_selected(str(table.get_row_at(table.cursor_row)[0]))
        elif action == "refresh":
            self.update_reference_settings()
            self.refresh_sessions()
        elif action == "pause" and self.busy:
            self.control.request("paused")
            self.query_one("#pause", Button).disabled = True
            self.query_one("#stage", Static).update("Pausing · saving the current operation…")
            self.query_one("#hint", Static).update(
                "An in-flight HTTP request may finish first. A submitted GPU job may continue; "
                "Resume reconnects to it."
            )
        elif action == "resume" and self.active:
            self.begin(self.active["name"])
        elif action in ("stop", "back"):
            if self.busy:
                self.return_home = True
                self.control.request("stopped")
                self.query_one("#stage", Static).update("Stopping · saving progress…")
                self.query_one("#pause", Button).disabled = True
            else:
                self.show_home()

    async def action_quit(self) -> None:
        # Also cover Textual's priority Ctrl+Q and command-palette quit action.
        self.action_quit_safely()

    def action_quit_safely(self) -> None:
        if self.busy:
            self.quitting = True
            self.control.request("stopped")
            self.query_one("#stage", Static).update("Saving progress before closing…")
        else:
            self.exit()
