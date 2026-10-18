"""Textual controls for resumable folder outpainting."""

from pathlib import Path

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Grid, Horizontal, Vertical, VerticalScroll
from textual.widgets import (
    Button,
    ContentSwitcher,
    Footer,
    Header,
    Input,
    Label,
    ProgressBar,
    RichLog,
    Static,
)

from .batch_outpaint import run_batch
from .control import RunControl, RunHalted


class OutpaintApp(App):
    TITLE = "Qwen · Folder Outpainting"
    BINDINGS = [
        Binding("q", "safe_quit", "Quit"),
        Binding("ctrl+q", "safe_quit", "Quit", priority=True, show=False),
        Binding("ctrl+p", "pause", "Pause"),
    ]
    CSS = """
    Screen { background: $background; }
    #settings { height: 19; padding: 1 3; border: round $primary; margin: 1 2; }
    .row { height: 3; }
    .row Label { width: 18; padding: 1 0; }
    .row Input { width: 1fr; }
    #parameters { grid-size: 2; grid-columns: 1fr 1fr; grid-rows: 3;
        grid-gutter: 0 3; height: 9; }
    #parameters .row Label { width: 15; }
    ContentSwitcher { height: 1fr; }
    #setup, #monitor { height: 1fr; }
    #setup-controls, #controls { height: 5; align: center middle; }
    Button { margin: 0 1; }
    #status, #endpoints, #setup-endpoints, #summary { margin: 0 3; height: auto; }
    #pipelines { height: 1fr; margin: 1 2; }
    .pipeline { width: 1fr; height: 1fr; border: round $primary; padding: 1; }
    #prompt-panel { margin-right: 1; }
    .pipeline-title { text-style: bold; color: $accent; height: 1; }
    .endpoint { height: auto; margin-bottom: 1; color: $text-muted; }
    ProgressBar { margin: 0 0 1 0; width: 1fr; }
    RichLog { height: 1fr; }
    """

    def __init__(self, config, *, repair=False):
        super().__init__()
        self.repair = repair
        if repair:
            self.title = "Qwen · Glasses Reflection Repair"
        self.config = config.copy()
        self.control = RunControl()
        self.busy = False
        self.quitting = False

    def compose(self) -> ComposeResult:
        yield Header()
        with ContentSwitcher(initial="setup", id="pages"):
            with Vertical(id="setup"):
                with VerticalScroll(id="settings"):
                    for key, label in (
                        ("source", "Source folder"),
                        ("destination", "Destination"),
                    ):
                        with Horizontal(classes="row"):
                            yield Label(label)
                            yield Input(str(self.config[key]), id=key)
                    with Grid(id="parameters"):
                        for key, label in self.fields():
                            with Horizontal(classes="row"):
                                yield Label(label)
                                yield Input(str(self.config[key]), id=key)
                yield Static(
                    f"Vision LLM: {self.config['llm']['base_url']}\n"
                    f"ComfyUI: {self.config['comfy']['base_url']}",
                    id="setup-endpoints",
                )
                with Horizontal(id="setup-controls"):
                    yield Button("Start / Resume", id="start", variant="primary")
            with Vertical(id="monitor"):
                yield Static(id="summary")
                with Horizontal(id="controls"):
                    yield Button("Resume", id="resume", variant="primary", disabled=True)
                    yield Button("Back to setup", id="back", disabled=True)
                    yield Button("Pause", id="pause", disabled=True)
                    yield Button("Stop", id="stop", disabled=True)
                yield Static(
                    "Ready · completed images and submitted jobs resume automatically", id="status"
                )
                with Horizontal(id="pipelines"):
                    with Vertical(id="prompt-panel", classes="pipeline"):
                        yield Label(
                            "Screening & prompts" if self.repair else "Prompt generation",
                            classes="pipeline-title",
                        )
                        yield Static(self.config["llm"]["base_url"], classes="endpoint")
                        yield ProgressBar(total=1, id="descriptions")
                        yield RichLog(id="prompt-log", wrap=True)
                    with Vertical(id="outpaint-panel", classes="pipeline"):
                        yield Label(
                            "Glasses repair" if self.repair else "Outpainting",
                            classes="pipeline-title",
                        )
                        yield Static(self.config["comfy"]["base_url"], classes="endpoint")
                        yield ProgressBar(total=1, id="renders")
                        yield RichLog(id="outpaint-log", wrap=True)
        yield Footer()

    def fields(self):
        common = (("steps", "Sampling steps"), ("cfg", "CFG"), ("jpeg_quality", "JPEG quality"))
        return (
            common
            if self.repair
            else (
                ("width", "Canvas width"),
                ("height", "Canvas height"),
                ("feathering", "Feathering"),
                *common,
            )
        )

    def report(self, kind, value):
        if kind in ("log", "prompts_log", "outpainting_log"):
            targets = {
                "log": ("prompt-log", "outpaint-log"),
                "prompts_log": ("prompt-log",),
                "outpainting_log": ("outpaint-log",),
            }
            for target in targets[kind]:
                self.query_one("#" + target, RichLog).write(value)
        elif kind == "progress":
            self.query_one("#descriptions", ProgressBar).update(
                total=value["total"], progress=value["described"]
            )
            self.query_one("#renders", ProgressBar).update(
                total=value.get("flagged", value["total"]), progress=value["rendered"]
            )
            self.query_one("#status", Static).update(
                f"{value['rendered']}/{value.get('flagged', value['total'])} saved · "
                f"{value.get('skipped', 0)} skipped · {value['failed']} failed"
            )

    def set_busy(self, busy):
        self.busy = busy
        for widget in self.query(Input):
            widget.disabled = busy
        for key in ("start", "resume", "back"):
            self.query_one("#" + key, Button).disabled = busy
        for key in ("pause", "stop"):
            self.query_one("#" + key, Button).disabled = not busy

    @on(Button.Pressed)
    def pressed(self, event):
        if event.button.id == "back" and not self.busy:
            self.query_one("#pages", ContentSwitcher).current = "setup"
            self.query_one("#source", Input).focus()
        elif event.button.id == "resume" and not self.busy:
            self.start_monitor()
        elif event.button.id in ("pause", "stop"):
            self.control.request("paused" if event.button.id == "pause" else "stopped")
            self.query_one("#status", Static).update(
                "Saving checkpoint… ComfyUI jobs remain resumable."
            )
        elif event.button.id == "start" and not self.busy:
            try:
                config = self.config.copy()
                for key in ("source", "destination"):
                    config[key] = str(
                        Path(self.query_one("#" + key, Input).value).expanduser().resolve()
                    )
                for key, _ in self.fields():
                    if key != "cfg":
                        config[key] = int(self.query_one("#" + key, Input).value)
                config["cfg"] = float(self.query_one("#cfg", Input).value)
                if config["source"] == config["destination"]:
                    raise ValueError("Choose different source and destination directories")
                if not self.repair and (
                    min(config["width"], config["height"], config["steps"]) < 1
                    or not 0 <= config["feathering"] <= 8192
                    or not 1 <= config["jpeg_quality"] <= 100
                    or not 0 < config["cfg"] <= 100
                ):
                    raise ValueError("Check dimensions, steps, feathering, CFG and JPEG quality")
                if self.repair:
                    from .batch_repair import validate

                    validate(config)
                self.config = config
            except ValueError as error:
                self.notify(str(error), severity="error")
                return
            for log in self.query(RichLog):
                log.clear()
            self.start_monitor()

    def start_monitor(self):
        self.query_one("#pages", ContentSwitcher).current = "monitor"
        self.query_one("#summary", Static).update(
            f"{self.config['source']} → {self.config['destination']}\n"
            + (
                "Glasses reflection repair"
                if self.repair
                else f"Canvas: {self.config['width']} × {self.config['height']}"
            )
        )
        self.query_one("#status", Static).update("Starting · preparing resumable jobs…")
        self.control = RunControl()
        self.set_busy(True)
        self.query_one("#pause", Button).focus()
        self.process()

    def show_status(self, message):
        self.query_one("#status", Static).update(message)
        self.report("log", message)

    @work(thread=True, exclusive=True)
    def process(self):
        try:
            runner = run_batch
            if self.repair:
                from .batch_repair import run_batch as runner
            result = runner(
                self.config, self.control, lambda k, v: self.call_from_thread(self.report, k, v)
            )
            self.call_from_thread(
                self.show_status,
                f"Finished: {result['rendered']} saved, {result.get('skipped', 0)} skipped, "
                f"{result['failed']} failed",
            )
        except RunHalted as error:
            self.call_from_thread(
                self.show_status, f"{error.status.title()} · resume or return to setup"
            )
        except Exception as error:
            self.call_from_thread(self.show_status, f"Error: {error}")
        finally:
            self.call_from_thread(self.set_busy, False)
            if self.quitting:
                self.call_from_thread(self.exit)

    def action_pause(self):
        if self.busy:
            self.control.request()

    def action_safe_quit(self):
        if self.busy:
            self.quitting = True
            self.control.request("stopped")
            self.query_one("#status", Static).update(
                "Quitting… waiting for active requests to finish and saving checkpoints."
            )
        else:
            self.exit()
