"""Textual controls for resumable folder outpainting."""

from pathlib import Path

from textual import on, work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Button, Footer, Header, Input, Label, ProgressBar, RichLog, Static

from .batch_outpaint import run_batch
from .control import RunControl, RunHalted


class OutpaintApp(App):
    TITLE = "Qwen · Folder Outpainting"
    BINDINGS = [("ctrl+q", "safe_quit", "Quit"), ("ctrl+p", "pause", "Pause")]
    CSS = """
    Screen { background: $background; }
    #settings { height: 19; padding: 1 3; border: round $primary; margin: 1 2; }
    .row { height: 3; }
    .row Label { width: 18; padding: 1 0; }
    .row Input { width: 1fr; }
    #controls { height: 5; align: center middle; }
    Button { margin: 0 1; }
    #status, #endpoints { margin: 0 3; height: auto; }
    ProgressBar { margin: 0 3; }
    RichLog { margin: 1 2; border: round $secondary; height: 1fr; }
    """

    def __init__(self, config):
        super().__init__()
        self.config = config.copy()
        self.control = RunControl()
        self.busy = False
        self.quitting = False

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll(id="settings"):
            for key, label in (
                ("source", "Source folder"),
                ("destination", "Destination"),
                ("width", "Canvas width"),
                ("height", "Canvas height"),
                ("feathering", "Feathering"),
                ("steps", "Sampling steps"),
                ("cfg", "CFG"),
                ("jpeg_quality", "JPEG quality"),
            ):
                with Horizontal(classes="row"):
                    yield Label(label)
                    yield Input(str(self.config[key]), id=key)
        yield Static(
            f"Vision LLM: {self.config['llm']['base_url']}\n"
            f"ComfyUI: {self.config['comfy']['base_url']}",
            id="endpoints",
        )
        with Horizontal(id="controls"):
            yield Button("Start / Resume", id="start", variant="primary")
            yield Button("Pause", id="pause", disabled=True)
            yield Button("Stop", id="stop", disabled=True)
        yield Static(
            "Ready · completed images and submitted jobs resume automatically", id="status"
        )
        yield Label("  Image descriptions")
        yield ProgressBar(total=1, id="descriptions")
        yield Label("  Finished outpaintings")
        yield ProgressBar(total=1, id="renders")
        yield RichLog(id="log", wrap=True)
        yield Footer()

    def report(self, kind, value):
        if kind == "log":
            self.query_one("#log", RichLog).write(value)
        else:
            self.query_one("#descriptions", ProgressBar).update(
                total=value["total"], progress=value["described"]
            )
            self.query_one("#renders", ProgressBar).update(
                total=value["total"], progress=value["rendered"]
            )
            self.query_one("#status", Static).update(
                f"{value['rendered']}/{value['total']} saved · {value['failed']} failed"
            )

    def set_busy(self, busy):
        self.busy = busy
        for widget in self.query(Input):
            widget.disabled = busy
        self.query_one("#start", Button).disabled = busy
        for key in ("pause", "stop"):
            self.query_one("#" + key, Button).disabled = not busy

    @on(Button.Pressed)
    def pressed(self, event):
        if event.button.id in ("pause", "stop"):
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
                for key in ("width", "height", "feathering", "steps", "jpeg_quality"):
                    config[key] = int(self.query_one("#" + key, Input).value)
                config["cfg"] = float(self.query_one("#cfg", Input).value)
                if config["source"] == config["destination"]:
                    raise ValueError("Choose different source and destination directories")
                if (
                    min(config["width"], config["height"], config["steps"]) < 1
                    or not 0 <= config["feathering"] <= 8192
                    or not 1 <= config["jpeg_quality"] <= 100
                    or not 0 < config["cfg"] <= 100
                ):
                    raise ValueError("Check dimensions, steps, feathering, CFG and JPEG quality")
                self.config = config
            except ValueError as error:
                self.notify(str(error), severity="error")
                return
            self.control = RunControl()
            self.set_busy(True)
            self.process()

    @work(thread=True, exclusive=True)
    def process(self):
        try:
            result = run_batch(
                self.config, self.control, lambda k, v: self.call_from_thread(self.report, k, v)
            )
            self.call_from_thread(
                self.report,
                "log",
                f"Finished: {result['rendered']} saved, {result['failed']} failed",
            )
        except RunHalted as error:
            self.call_from_thread(
                self.report, "log", f"{error.status.title()} · use Start / Resume to continue"
            )
        except Exception as error:
            self.call_from_thread(self.report, "log", f"Error: {error}")
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
        else:
            self.exit()
