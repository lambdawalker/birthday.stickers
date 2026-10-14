"""Textual session picker and responsive generation dashboard."""

import copy
import logging

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
)

from .config import Config, load_config
from .control import RunControl
from .sessions import SessionStore


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
    .field { width: 1fr; height: auto; margin-right: 2; }
    Input { background: #1b2233; border: tall #565277; }
    Input:focus { border: tall #b7a1e8; }
    .buttons { height: auto; margin-top: 0; }
    Button { margin-right: 2; min-width: 16; }
    Button.-primary { background: #b7a1e8; color: #101522; }
    Button.-success { background: #8dd5c8; color: #101522; }
    Button.-warning { background: #f5dba7; color: #101522; }
    #sessions { height: 6; background: #1b2233; }
    #session-title { color: #e5c9fa; text-style: bold; }
    #stage { margin: 1 0; color: #a9d9ed; }
    #workers { height: 1fr; min-height: 16; }
    .worker { width: 1fr; height: 100%; border: round #565277; padding: 0 1; }
    #prompt-worker { margin-right: 1; }
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

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll(id="type-picker"):
            yield Static("What would you like to create?", classes="title")
            yield Static(
                "Choose a generation type to start or resume a collection.", classes="muted"
            )
            with Vertical(classes="card"):
                yield Label("GIRL + DRAGON", classes="title")
                yield Static("Two character references · Qwen Edit + outpainting", classes="muted")
                yield Button("Girl + dragon", variant="primary", id="choose-duo")
            with Vertical(classes="card"):
                yield Label("BABY DRAGON ONLY", classes="title")
                yield Static(
                    "300 solo activities · text-to-image · no references needed", classes="muted"
                )
                yield Button("Baby dragon only", variant="success", id="choose-dragon")
            yield Static("", id="type-error")
        with VerticalScroll(id="home"):
            yield Static("A new adventure, or right where you left off.", classes="title")
            yield Static(
                "Create a collection, or resume any unfinished session.",
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
                with Horizontal(classes="buttons"):
                    yield Button("New session", variant="primary", id="new")
                    yield Button("Change type", id="change-type")
                yield Static(
                    "Prompts/settings are saved; new scenes use live character references.",
                    classes="muted",
                )
            yield Label("UNFINISHED SESSIONS", classes="title")
            yield DataTable(id="sessions", cursor_type="row")
            yield Static("", id="empty", classes="muted")
            with Horizontal(classes="buttons"):
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
                with Vertical(id="prompt-worker", classes="worker"):
                    yield Label("LLM · PROMPTS", classes="title")
                    yield Static("Waiting to start", id="prompts-stage", classes="worker-stage")
                    yield ProgressBar(total=100, show_eta=False, id="prompts-progress")
                    yield Static("", id="prompts-count", classes="worker-count")
                    yield RichLog(
                        id="prompts-logs", wrap=True, min_width=1, markup=False, max_lines=1000
                    )
                with Vertical(id="image-worker", classes="worker"):
                    yield Label("COMFYUI · IMAGES", classes="title")
                    yield Static("Waiting for prompts", id="images-stage", classes="worker-stage")
                    yield ProgressBar(total=100, show_eta=False, id="images-progress")
                    yield Static("", id="images-count", classes="worker-count")
                    yield RichLog(
                        id="images-logs", wrap=True, min_width=1, markup=False, max_lines=1000
                    )
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#sessions", DataTable).add_columns(
            "Session", "Type", "Progress", "Status", "Last step"
        )
        self.refresh_sessions()
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
        items = self.store.list_incomplete()
        for item in items:
            status = item["status"]
            if status == "running":
                status = "Running / interrupted"
            table.add_row(
                item["name"],
                item.get("generation_type", "viking_duo"),
                f"{item.get('prompts_completed', item.get('completed', 0))} prompts / "
                f"{item.get('completed', 0)} images / {item['count']}",
                status,
                item.get("stage", ""),
                key=item["name"],
            )
        self.query_one("#open", Button).disabled = not items
        text = (
            "Select a session and press Resume. Enter also opens the selected row."
            if items
            else ("No unfinished sessions yet. Start your first collection above.")
        )
        if self.store.errors:
            text += "\nUnreadable sessions: " + "; ".join(self.store.errors)
        self.query_one("#empty", Static).update(Text(text))
        self.query_one("#new-name", Input).value = self.store.next_name()

    def choose_type(self, generation_type: str) -> None:
        if self.busy:
            return
        try:
            if generation_type == self.launch_config.generation.type:
                selected = copy.deepcopy(self.launch_config)
            else:
                stem = "config.dragon" if generation_type == "baby_dragon" else "config"
                directory = self.launch_config.path.parent
                local = directory / f"{stem}.local.yaml"
                path = local if local.is_file() else directory / f"{stem}.yaml"
                selected = load_config(path)
                if selected.generation.type != generation_type:
                    raise ValueError(f"{path} must use generation.type: {generation_type}")
                # Connection settings and the session collection belong to the launcher.
                selected.llm = copy.deepcopy(self.launch_config.llm)
                selected.comfy = copy.deepcopy(self.launch_config.comfy)
                selected.output.directory = self.launch_config.output.directory
                selected.generation.count = self.launch_config.generation.count
            self.config = selected
        except (OSError, ValueError) as error:
            self.query_one("#type-error", Static).update(Text(f"Unable to load this type: {error}"))
            return
        self.query_one("#new-type", Label).update(f"NEW SESSION · {generation_type}")
        self.query_one("#count", Input).value = str(selected.generation.count)
        self.query_one("#type-error", Static).update("")
        self.show_home()
        self.query_one("#new", Button).focus()

    def show_dashboard(self) -> None:
        self.query_one("#type-picker").display = False
        self.query_one("#home").display = False
        self.query_one("#dashboard").display = True
        for lane in ("prompts", "images"):
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
        self.query_one("#session-title", Static).update(name or self.store.next_name())
        self.query_one("#stage", Static).update("Preparing session…")
        self.query_one("#totals", Static).update("")
        self.query_one("#output", Static).update("")
        for lane in ("prompts", "images"):
            self.query_one(f"#{lane}-progress", ProgressBar).update(total=count or 100, progress=0)
            self.query_one(f"#{lane}-stage", Static).update("Waiting to start")
            self.query_one(f"#{lane}-count", Static).update("")
        self.execute_session(name, count)

    @work(thread=True, exit_on_error=False)
    def execute_session(self, name: str | None, count: int | None) -> None:
        try:
            if name is None:
                item = self.store.create(self.config, count)
                name = item["name"]
            self.post_message(SessionUpdate(self.store.read(name)))
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
        self.query_one("#session-title", Static).update(
            Text(item["name"] + " · " + item.get("generation_type", "viking_duo"))
        )
        status = item["status"].upper()
        if self.busy and self.control.requested.is_set():
            status = "SAVING BOTH WORKERS"
        self.query_one("#stage", Static).update(Text(status))
        prompt_count = item.get("prompts_completed", item.get("completed", 0))
        image_count = item.get("completed", 0)
        stages = {
            "planning": "Planning scene",
            "refining": "Refining Qwen prompt",
            "prompts_ready": "Prompt saved to queue",
            "done": "Finished",
            "waiting_for_prompts": "Waiting for a ready prompt",
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
            f"{max(0, prompt_count - image_count)} awaiting final images"
        )
        self.query_one("#output", Static).update(
            Text(f"Images: {self.store.directory(item['name']) / 'final'}")
        )
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
        lanes = (event.worker,) if event.worker in ("prompts", "images") else ("prompts", "images")
        for lane in lanes:
            self.query_one(f"#{lane}-logs", RichLog).write(Text(event.text))

    @on(DataTable.RowSelected, "#sessions")
    def selected(self, event: DataTable.RowSelected) -> None:
        self.begin(str(event.row_key.value))

    @on(Button.Pressed)
    def pressed(self, event: Button.Pressed) -> None:
        action = event.button.id
        if action in ("choose-duo", "choose-dragon"):
            self.choose_type("viking_duo" if action == "choose-duo" else "baby_dragon")
        elif action == "change-type" and not self.busy:
            self.query_one("#home").display = False
            self.query_one("#type-picker").display = True
            self.query_one("#choose-duo", Button).focus()
        elif action == "new":
            try:
                count = int(self.query_one("#count", Input).value)
                if count < 1:
                    raise ValueError()
            except ValueError:
                self.notify("Enter a positive number of stickers.", severity="error")
                return
            self.active = None
            self.begin(count=count)
        elif action == "open":
            table = self.query_one("#sessions", DataTable)
            if table.row_count:
                self.begin(str(table.get_row_at(table.cursor_row)[0]))
        elif action == "refresh":
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
