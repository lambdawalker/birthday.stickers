"""Textual session picker and responsive generation dashboard."""

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

from .config import Config
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
    def __init__(self, text: str):
        super().__init__()
        self.text = text


class UILogHandler(logging.Handler):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.setFormatter(logging.Formatter("%(asctime)s  %(message)s", datefmt="%H:%M:%S"))

    def emit(self, record):
        self.app.post_message(LogLine(self.format(record)))


class StickerApp(App):
    TITLE = "Birthday Stickers"
    SUB_TITLE = "Viking storybook studio"
    BINDINGS = [("q", "quit_safely", "Quit"), ("ctrl+c", "quit_safely", "Save & quit")]
    CSS = """
    Screen { background: #101522; color: #e7e4f2; }
    Header { background: #24243d; color: #e5c9fa; }
    Footer { background: #24243d; }
    #home, #dashboard { padding: 1 3; }
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
    #progress { width: 100%; margin: 1 0; }
    #output, #hint { color: #acb4ca; height: auto; }
    #error { color: #ffb4bc; height: auto; margin: 0; }
    #logs { height: 1fr; min-height: 6; border: round #565277; background: #151c2c; }
    #dashboard { display: none; }
    """

    def __init__(self, config: Config, store: SessionStore | None = None):
        super().__init__()
        self.config = config
        self.store = store or SessionStore(config.output.directory / "sessions")
        self.control = RunControl()
        self.busy = False
        self.active: dict | None = None
        self.return_home = False
        self.quitting = False
        self.handler = UILogHandler(self)

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll(id="home"):
            yield Static("A new adventure, or right where you left off.", classes="title")
            yield Static(
                "Create a collection of Viking scenes with your baby girl and dragon.",
                classes="muted",
            )
            with Vertical(classes="card"):
                yield Label("NEW SESSION", classes="title")
                with Horizontal(classes="fields"):
                    with Vertical(classes="field"):
                        yield Label("Session name · automatic")
                        yield Input(self.store.next_name(), disabled=True, id="new-name")
                    with Vertical(classes="field"):
                        yield Label("Number of stickers")
                        yield Input(str(self.config.generation.count), type="integer", id="count")
                with Horizontal(classes="buttons"):
                    yield Button("New session", variant="primary", id="new")
                yield Static(
                    "Your settings and references are saved with each session.", classes="muted"
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
                yield ProgressBar(total=100, show_eta=False, id="progress")
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
            yield RichLog(id="logs", wrap=True, highlight=False, markup=False, max_lines=1000)
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#sessions", DataTable).add_columns(
            "Session", "Progress", "Status", "Last step"
        )
        self.refresh_sessions()
        logger = logging.getLogger("birthday_stickers")
        self.old_log_level = logger.level
        logger.setLevel(logging.INFO)
        logger.addHandler(self.handler)

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
                f"{item.get('completed', 0)} / {item['count']}",
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

    def show_dashboard(self) -> None:
        self.query_one("#home").display = False
        self.query_one("#dashboard").display = True
        self.query_one("#logs", RichLog).clear()
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
        self.query_one("#progress", ProgressBar).update(total=count or 100, progress=0)
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
        self.query_one("#session-title", Static).update(Text(item["name"]))
        self.query_one("#stage", Static).update(
            Text(f"{item['status'].upper()}  ·  {item.get('sticker', '')}  {item.get('stage', '')}")
        )
        self.query_one("#progress", ProgressBar).update(
            total=item["count"], progress=item.get("completed", 0)
        )
        self.query_one("#totals", Static).update(
            f"{item.get('completed', 0)} of {item['count']} stickers saved"
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
        self.query_one("#logs", RichLog).write(Text(event.text))

    @on(DataTable.RowSelected, "#sessions")
    def selected(self, event: DataTable.RowSelected) -> None:
        self.begin(str(event.row_key.value))

    @on(Button.Pressed)
    def pressed(self, event: Button.Pressed) -> None:
        action = event.button.id
        if action == "new":
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
