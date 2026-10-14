# Birthday Stickers

Generate a batch of illustrated scenes with two consistent character references.
An OpenAI-compatible LLM plans each scene and refines it with a local Qwen guide;
ComfyUI renders it with Qwen 2.1 and extends it with Flux Fill to your final size.

The default command opens a Textual session picker and live generation dashboard.
Each session has its own folder under `output/sessions/YYYY-MM-DD-NNN/`.

Three workers form a pipeline: the LLM prepares prompts for upcoming scenes while
ComfyUI renders earlier ones. For each of N scenes (paths below are relative to
that session folder):

1. Read `src/birthday_stickers/flows/multi_character_reference/planning.md` and ask LLM A for a raw scene.
2. Assemble the raw scene, local Qwen guide, and refinement template locally; ask
   the same LLM for the refined Qwen editing instruction.
3. Upload the protagonist to Qwen input 1 and the fantastic creature to input 2.
4. Run the Qwen workflow and save `image_generation/results/stiker-0000.jpg`.
5. Upload that JPEG, compute centered padding, and run Flux with the **raw** scene.
6. Save `outpainting/results/stiker-0000.jpg` at the exact configured width and height.
7. Keep both descriptions in `scene_planning/results/stiker-0000.md`, plus job metadata
   and the exact submitted graphs for troubleshooting.

## Quick start (Windows PowerShell)

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then:

```powershell
git clone https://github.com/lambdawalker/birthday.stickers.git
cd birthday.stickers
uv sync --locked
Copy-Item config.yaml config.local.yaml
```

Add character references under `assets/characters/`, named `A000.jpg`, `A001.jpg`,
`B000.jpg`, `B001.jpg`, etc. The prefix is an uppercase character ID from A–Z;
the suffix is a numeric reference number. These images are not yet included in the repo.

Edit `config.local.yaml`, especially:

```yaml
llm:
  base_url: http://192.168.1.100:1234/v1
  model: your-loaded-model-id
comfy:
  base_url: http://192.168.1.101:8188
generation:
  count: 10
  width: 1024
  height: 1024
output:
  width: 1536
  height: 1536
```

The snippet shows fields to edit; retain the other sections of the supplied file.
Replace both example IPs and the model ID with your actual servers. LLM A must
expose `/v1/chat/completions`; the ComfyUI URL should point to its root, not `/v1`.
Ensure both servers listen on a network interface reachable from this computer.
If ComfyUI is local, its usual startup option for LAN access is `--listen 0.0.0.0`.

```powershell
# Validate local files and mappings, without contacting either server:
uv run birthday-stickers --config config.local.yaml --dry-run

# Also check the LLM model and ComfyUI nodes/model filenames, without generating:
uv run birthday-stickers --config config.local.yaml --check

# Open the session picker (optionally prefill a one-sticker session):
uv run birthday-stickers --config config.local.yaml --count 1
uv run birthday-stickers --config config.local.yaml
```

Linux/macOS use the same commands, with `cp config.yaml config.local.yaml`.
Python 3.11+ is supported. No CUDA/PyTorch installation is needed on the client;
the LLM and ComfyUI servers run the models.

## Sessions and controls

Run `uv run birthday-stickers --config config.local.yaml` to open the interface.
The home screen lists saved sessions, including completed ones, with progress and last stage.
Choose **New session**, or select a row, adjust its outpainting toggle, and choose
**Resume selected**. Clicking or pressing Enter selects a row without starting work. Names are automatically filled from your computer's local
date plus a daily counter, for example `2026-10-14-001` and `2026-10-14-002`.

The active dashboard has **LLM / Prompts**, **ComfyUI / Images**, and
**ComfyUI / Outpainting** panels.
Each shows its active server URL, current sticker and step, a progress bar, and its own timed log.
URLs come from the active session snapshot; outpainting shows the effective
endpoint, including fallback to the generation server.
The prompt bar counts scenes with both raw and refined prompts saved; the image
bar counts generated images saved. The outpainting bar counts completed final
images when enabled. The summary shows final output count and effective GPU mode.

- **Pause:** checkpoint all workers and stay on the dashboard; **Resume** continues.
- **Stop:** save progress and return to the session picker. The session stays resumable.
- **Sessions:** return to the picker when generation is paused, finished, or failed.
- **Q / Ctrl+C / Ctrl+Q:** save and quit. On restart, unfinished sessions remain listed.

Controls are cooperative: all workers stop starting new operations, and an
in-flight LLM or HTTP request finishes or times out before pausing. ComfyUI polling can pause between requests. Already-submitted GPU
jobs may continue on the server; resuming reconnects using the saved job ID.
The app never sends a global ComfyUI interrupt that could affect other users.

The LLM can prepare the entire remaining session without waiting for images. A
scene enters the renderer's queue only after both prompts and their metadata are
saved. On restart the queue is rebuilt from these records, so ready prompts are
reused, partial LLM work resumes from the saved raw description, and submitted GPU
jobs reconnect by ID. Scene numbers, character choices, and activity history stay
consistent even when the prompt worker is far ahead. If any worker fails, the
others stop at their next checkpoint; in-flight responses are saved before the
session reports the original error. Existing sequential sessions can also resume.

Log lines include durations, for example `Plan scene finished in 2.31s` and
`Qwen render and download finished in 43.72s`. Timed steps cover planning,
refinement, reference uploads, Qwen rendering/download, intermediate JPEG saving,
intermediate upload, Flux rendering/download, and final compositing/saving.
Durations are elapsed wall time for each active attempt, including server queue
and network waits. Paused time between runs is excluded; a resumed job gets a new
attempt entry. Failed and paused attempts are labeled accordingly. The per-sticker
`metadata/*.json` files retain the timing history under `timings` with the step,
seconds, outcome, and recording timestamp. These are client-observed durations,
not GPU-only inference measurements.

Each session stores `session.json`, a `config.yaml` snapshot, copies of the prompts
and workflows under `inputs/`, plus stage folders and `metadata/`. Changing the main YAML affects new sessions only. To
change a server address for an existing session, edit its saved `config.yaml`
while stopped. Keep dimensions and workflows unchanged for partial
scenes. API credentials still come from the configured environment variables.

Only one process can run a session at a time. Process locks release automatically
on exit or a crash. A stale **Running / interrupted** entry can be resumed after
its previous process exits. A new session uses a separate output folder, so sticker
names can start at `stiker-0000.jpg` again without overwriting another session.

For scripts or terminals without an interactive display:

```powershell
uv run birthday-stickers --config config.local.yaml --headless --count 10
uv run birthday-stickers --config config.local.yaml --headless --resume 2026-10-14-001
```

`--count` sets the total for a new session; resuming retains the saved session total.
`--dry-run` and `--check` remain noninteractive and do not create a session.

If a submission connection fails before its job ID is received, the app leaves an
explicit pending-submission marker instead of risking a duplicate GPU job. Inspect
ComfyUI's queue/history, then add the known `qwen_prompt_id` or `flux_prompt_id` to
that sticker's metadata. Remove the matching `*_submission_pending` marker only
if you confirm no job was queued. A job that failed on ComfyUI requires fixing the
server issue and deliberately clearing that stage's job ID before retrying. Keep
server history and output files available until the session has downloaded them.

## Configuration

All relative paths resolve against the YAML file's directory, regardless of the
current working directory. In Windows YAML paths, prefer forward slashes or
single quotes, for example `'D:\Art\protagonist.png'`.

| Setting | Purpose |
|---|---|
| `llm.base_url`, `llm.model` | IP A, port, OpenAI API prefix, and loaded model ID |
| `comfy.base_url` | IP B and ComfyUI port |
| `characters.directory` | Folder containing all character reference variants |
| `characters.protagonist`, `characters.creature` | Character IDs (default A and B), first and second socket |
| `generation.count` | Default total stickers for a new session; editable in the interface |
| `output.directory` | Parent folder containing the `sessions/` directory |
| `generation.width`, `generation.height` | Qwen output canvas; multiples of 32 |
| `output.width`, `output.height` | Exact final width M and height N in pixels |
| `output.jpeg_quality` | Default 95; lower values save more space |
| `output.jpeg_subsampling` | Default 0 (4:4:4) for illustration edges; 2 makes smaller files |
| `output.feathering` | Blended boundary between the original and generated extension |
| `output.preserve_center` | Restore the original center with edge feathering; default true |
| `generation.seed` | `null` for random; integer for seed + 2 × sticker index, then +1 for Flux |
| `llm.send_reference_images` | Send the ordered reference images to a vision-capable LLM |
| `llm.extra_body` | Server-specific OpenAI request fields, such as thinking controls |
| `workflows.*.overrides` | Override existing node inputs such as checkpoints, steps, or guidance |

The checked-in defaults preserve the supplied samplers: Qwen 21 steps / CFG 1.4;
Flux 20 steps / CFG 1, with Flux guidance 30. They are workflow settings, not a
claim of optimal model parameters. See [workflow details](src/birthday_stickers/flows/README.md) for
all node bindings and reference-resolution tuning.

Character references are discovered automatically from the configured directory:

```yaml
characters:
  directory: assets/characters
  protagonist: A
  creature: B
```

For each scene, the program independently chooses one matching file per character
at random. For example, it can use `A003.jpg` with `B012.jpg`, then `A000.jpg` with
`B002.jpg`. Repeats are allowed. Any uppercase ID A–Z can be assigned to either
role; the protagonist always goes to Qwen socket 1, and the creature to socket 2.
Reference numbers may have any number of digits; both `.jpg` and `.JPG` are accepted.
Other filenames and subdirectories are ignored. Missing or invalid references
are reported before generation starts.

The selected pair stays fixed throughout that scene's planning, refinement, and
Qwen rendering, including optional LLM vision input. Metadata records the actual
selected paths and hashes. References already uploaded during the batch are reused
when selected again. Reference selection is random independently of the configured
diffusion seed. Existing configurations specifying explicit `protagonist` and
`creature` file paths without `directory` continue to use those fixed files.

An LLM with image input support can inspect both references during planning and
refinement. With the default `send_reference_images: false`, it receives role
descriptions only; Qwen still receives both actual full-size reference files.
Vision requests use JPEG previews up to 1536 px on the long side. Use a context
window large enough for the full guide, template, and response (32K is a useful
starting configuration, especially with vision).

For authenticated servers, configure an environment-variable name rather than
putting tokens in YAML:

```yaml
llm:
  api_key_env: LLM_API_KEY
comfy:
  api_key_env: COMFY_API_KEY
```

```powershell
$env:LLM_API_KEY = "your-token"
$env:COMFY_API_KEY = "your-token"
```

Leave `api_key_env: null` for servers without authentication. The client sends
configured tokens as Bearer headers. It never stores their values in output metadata.

## Prompts and the Qwen guide

Edit [the scene brief](src/birthday_stickers/flows/multi_character_reference/planning.md) to set theme, medium, activities,
subjects, or any requested lettering. Its optional placeholders are
`{{iteration}}`, `{{count}}`, `{{sticker_id}}`, and `{{previous_scenes}}`. The last
placeholder includes up to ten earlier planned scenes from the session to
encourage variety; it is not a guarantee against duplicate compositions.

The [refinement template](src/birthday_stickers/flows/multi_character_reference/refinement.md) must contain `{{guide}}` and
`{{scene}}`. Both are substituted locally in one pass. The raw description is
sent unchanged to Flux's positive prompt node; it is never replaced by Qwen's
reference-tagged refinement.

The [supplied guide](src/birthday_stickers/flows/multi_character_reference/guide.md) is included with
a project-specific supplement and corrections distinguishing official T2I and
editing contracts. Both characters are **identity references for a new scene**;
the first image is not automatically a base canvas. The pipeline refines once,
bypassing the exported Qwen workflow's internal prompt enhancer.

## Dimensions and output files

For source width W and requested width M, the desired left padding is
`(M - W) // 2`, with the remainder on the right. Height works the same way.
The script measures the actual intermediate JPEG before calculating this.

Flux gets a working canvas rounded up to multiples of 16. After generation,
the script crops only the additional outer margins to deliver the exact target.
For example, a 1024 × 1024 intermediate into 1600 × 1200 needs 288 px on each
horizontal side and 88 px on each vertical side. Odd remainders go right/bottom.
The central image is never stretched or cropped. A target smaller than the source
is rejected. An equal-size target skips the redundant Flux job.

The default central composite protects the original subjects from diffusion/VAE
changes, while feathering allows a smooth border. JPEG compression means the saved
pixels are not bit-for-bit identical. Turn `preserve_center` off to retain Flux's
whole decoded result. JPEG has no alpha channel; transparent input is flattened
against `output.jpeg_background`.

| Folder | Files |
|---|---|
| `image_generation/results/` | `stiker-0000.jpg`, `stiker-0001.jpg`, … |
| `outpainting/results/` | Matching JPEG names at the requested dimensions |
| `scene_planning/results/` | One matching Markdown file with raw and refined descriptions |
| `metadata/` | Status, seeds, reference hashes, padding, prompt IDs, submitted JSON graphs |

The requested spelling **`stiker`** is intentional. IDs start at zero within each
session. Resuming skips completed images and continues the unfinished sticker from
its saved stage, using the same references, prompts, seeds, and ComfyUI job IDs.
Keep session metadata and inputs alongside the images. Legacy output from before
sessions remains untouched and is not automatically imported into the session list.

Local images are JPEG. ComfyUI still keeps its own generated PNGs and uploaded input
files on IP B, so clean those through your normal server maintenance if needed.

## Required ComfyUI setup

Use a current ComfyUI version that includes `TextEncodeQwenImage21` and
`QwenImage21Cache`, with the model files referenced by the supplied workflows:

| Model directory | Filename |
|---|---|
| `models/diffusion_models/` | `qwen_image_2.1_int8_convrot.safetensors` |
| `models/text_encoders/` | `qwen3vl_8b_int8_convrot.safetensors` |
| `models/vae/` | `qwen_image_2.1_vae_bf16.safetensors` |
| `models/diffusion_models/` | `flux1-fill-dev.safetensors` |
| `models/text_encoders/` | `clip_l.safetensors`, `t5xxl_fp16.safetensors` |
| `models/vae/` | `ae.safetensors` |

Alternative checkpoints/quantizations may need different loaders; this client
does not download weights or install server nodes. The internal Qwen prompt
enhancer checkpoint is not required. `--check` reports absent active node classes
and missing model dropdown entries before you spend time on generation.

## Failure handling

- Raw prompts are saved before refinement; both prompts are saved before rendering.
- Prompt IDs are recorded immediately after each accepted submission.
- Transient GET failures retry with bounded exponential delays. POSTs never retry
  automatically because the server may already have accepted them.
- Polling uses `/history/{prompt_id}` and retrieves only the configured output node.
  It reports execution errors and has a configurable timeout that includes queue time.
- Timeouts or Ctrl+C leave server jobs alone. Check ComfyUI's queue/history and the
  saved prompt ID before manually submitting again. No global interrupt is sent.
- A failure stops the session and displays the error in the dashboard. Headless
  mode returns a nonzero exit code. Saved scenes and partial artifacts remain.

If your LLM reports HTTP 400, check its model ID, supported token-limit field,
context window, and whether it supports `image_url` input. No tool-calling support
is required. For models that expose reasoning in the output, configure the server's
thinking/parser controls using `extra_body` rather than sending reasoning to Qwen.

## Development and validation

```powershell
uv sync --locked
uv run python -m unittest discover -s tests -v
uv run ruff check .
uv run ruff format --check .
uv build
```

Tests use actual local HTTP servers to exercise uploads, LLM requests, workflow
submission, history polling, output downloads, and disk artifacts. They do not
load AI models or verify visual quality. CI runs the suite on Windows and Linux.
Live generation still needs your character images and reachable model servers.

## Diverse Viking scenes

The default planning prompt features a baby girl and baby dragon in a cozy Viking
storybook world. Backgrounds use blush pink, lavender, powder blue, pale butter
yellow, and peach, with soft gradients and sparse white sparkles. Character colors
and the girl's Viking outfit remain faithful to the chosen references. The planner
includes this art direction in the raw scene, which also guides Flux outpainting;
the refinement prompt explicitly preserves it for Qwen.

Each flow defaults to its own activity catalog, for example `src/birthday_stickers/flows/multi_character_reference/activities.json`: 300 editable
activities in 12 categories, each with compatible settings. For each scene the
program samples an unused activity, avoids the preceding category when possible,
and selects a setting, mood, framing, and pastel palette. The LLM supplies the
specific poses and delightful details. The sampler is independent of diffusion
seeds. Set `prompts.activities: null` to use only your planning prompt.

Selection history lives in the per-sticker JSON files under each session's `metadata/`.
It persists when resuming that session. New sessions start a fresh activity cycle. Every activity is used
before a new cycle starts; failed attempts also consume their chosen activity.
The selected brief is saved before the LLM request and appears in the prompt
Markdown alongside the raw and refined descriptions. Keep metadata to retain
history; use a new output directory to start a fresh collection. Run only one
process per output directory when using activity sampling.

Catalog IDs must be unique and stable. Each record needs `id`, `category`,
`activity`, and a nonempty `settings` list. Editing the catalog recalculates history
against the currently available IDs; adding activities makes them available in the
current cycle. Reference images remain local and are not embedded in the catalog.


### Generation types

- `multi_character_reference` (default `config.yaml`): baby girl + baby dragon identity references,
  Qwen Edit with optional Flux outpainting.
- `single_character_text` (`config.dragon.yaml`): one baby dragon, 300 dedicated activities,
  local LLM planning/refinement, and the supplied Qwen Image 2.1 text-to-image flow.
  No references are needed; Flux is needed only with outpainting enabled.
  The attached Qwen flow has no image sockets;
  edit `src/birthday_stickers/flows/single_character_text/planning.md` to customize the dragon's appearance.

The interactive app opens with a generation-type picker. Choose **Girl + dragon**
or **Baby dragon only**, then create a session or resume a saved one.
Use **Change type** on the session screen to return to the picker.

Start the app normally (including your personal server settings):

```sh
uv run birthday-stickers --config config.local.yaml
```

The selected type loads its configuration from the launcher YAML's directory:
`config.local.yaml` / `config.yaml` for the duo, and
`config.dragon.local.yaml` / `config.dragon.yaml` for the solo dragon (local files
take priority). The type matching the launcher uses that exact launch configuration.
Switching types retains the launcher's LLM/Comfy connection settings, count, and
output directory. Prompts, workflows, canvas size and JPEG settings come from the
selected type. Keep both configuration files together. Existing sessions always
resume with their own saved settings, regardless of the selected new-session type.

For headless runs, choose the configuration explicitly:

```sh
uv run birthday-stickers --config config.dragon.yaml --headless
uv run birthday-stickers --config config.yaml --headless
```

Both types share the session picker, pause/stop/resume, three pipeline
workers, per-step timings, Markdown prompts, and `stiker-0000.jpg` naming.
The session records its type; resuming uses its saved configuration. Both configs
use the same output directory, so either launcher lists all incomplete sessions.
For both types, `generation.width/height` set the Qwen canvas. With outpainting
off, those are also the final JPEG dimensions. With outpainting on,
`output.width/height` set the final size and must be at least the canvas size.
The workflow's internal text generator, resolution selector and seed node are
pruned in favor of the local prompt, configured canvas and durable scene seed.

### Live character references

New sessions no longer copy character images. They retain the absolute source
folder (or explicit legacy image paths). Before each new duo scene, the program
rescans `[A-Z][0-9]+.jpg`, including `A0.jpg`, `A100.jpg`, `B1000.jpg` and
`C123456.jpg`; there is no digit limit. Set the two role labels in YAML, e.g.
`protagonist: A` and `creature: C`. New files become eligible immediately for
unplanned scenes, including after a resume. Already-planned scenes keep their
selected paths and hashes: do not delete or overwrite those files until their
images finish. You can change references freely between sessions.

Older sessions retain their original snapshot paths for safe resumption; their
existing backups are not deleted or automatically migrated. New sessions do not
create these backups. Prompt/activity/workflow snapshots are still retained.


### Optional outpainting

The **Outpainting** switch beside **New session** controls new sessions.
**Change type** is below those controls. A separate **Outpainting** switch beside
**Resume selected** follows the highlighted saved-session row, including completed
sessions. Change it and press **Resume selected** to apply the setting
and resume. Switching rows discards an unapplied toggle change.
`generation.outpainting: true/false` sets the initial switch value and controls
headless runs. The duo default is on; the solo dragon default is off.

With outpainting on, either Qwen flow saves an intermediate JPEG, then uses the
original raw scene prompt with Flux. Padding centers the source in the configured
final canvas. With it off, Qwen saves to `image_generation/results/` and Flux is
not validated, uploaded to, or run. Existing sessions without this setting retain
the old behavior: duo on, solo dragon off.

Both flows inherit `workflows.flux` from `flows/common/flow.yaml`. For an older
flat configuration, migrate to `flow: single_character_text` and remove its
`prompts`/`workflows` sections to use these defaults. Keep custom model overrides
and choose larger `output.width/height` before enabling the switch.
The switch is saved in the session snapshot, along with the chosen dimensions.


### Avoiding foggy outpaint borders

The shared `flows/common/flow.yaml` uses 50 Flux Fill steps (node `3`) instead of the supplied
workflow's 20, retaining guidance 30, CFG 1 and denoise 1. This matches the model
publisher's example sampling budget: https://huggingface.co/black-forest-labs/FLUX.1-Fill-dev.
It is a quality-oriented starting point, not a guarantee of sharper results, and
increases sampling time. Keep denoise at 1 for newly padded empty regions.

The resolved `outpainting.md` file appends local continuity instructions to the **original
raw scene**, requesting consistent detail, lighting and focus rather than a vignette
or blurred frame. Added regions must contain scenery only, with no new characters
or duplicated faces/bodies; an existing character crossing the boundary may be
continued anatomically. The exact combined prompt is saved as `outpainting_prompt` in
scene metadata and in the submitted Flux graph. Set `prompts.outpainting: null`
for raw-only behavior in a flow-based config.
The 24-pixel feather and center preservation remain unchanged: they only blend the
source boundary, and do not blur the entire generated border.

Flow-based configs inherit these defaults. Existing session snapshots
and already-submitted jobs retain their previous settings. Compare new sessions with
the same scene/seed before deciding whether the new settings improve your output.
For a paused session, changes to its saved YAML only affect Flux jobs not yet submitted;
never clear a saved job ID to rerender a job that may still be running.


### Flow module and prompt lookup

All generation-specific assets live in the Python package `src/birthday_stickers/flows/`:

| Folder | Contents |
|---|---|
| `multi_character_reference/` | Qwen Edit builder, LLM instructions, `flow.yaml`, `planning.md`, `refinement.md`, `guide.md`, `activities.json`, `qwen.json` |
| `single_character_text/` | Text-to-image builder, LLM instructions, `flow.yaml`, its own prompts/guide/activity catalog and `qwen.json` |
| `common/` | Shared `outpainting.md`, Flux workflow and `flow.yaml` bindings/settings |

Select a flow in the launch YAML:

```yaml
flow: multi_character_reference
# or: single_character_text
```

Prompt filenames are searched **in the selected flow folder first, then in
`flows/common/`**. For example, adding `single_character_text/outpainting.md`
overrides the shared version only for that flow. A missing file in both locations
produces an error naming both searched directories. The same resolver is used for
default workflow assets. Activity catalogs are resolved like prompt files.

Optional config overrides still work:

```yaml
prompts:
  planning: ./my-prompts/planning.md  # explicit path, relative to this YAML
  outpainting: outpainting.md       # flow folder, then common
workflows:
  flux:
    overrides:
      "3": {steps: 40}              # merged with the flow's workflow defaults
```

Absolute paths and paths containing directories resolve relative to the YAML
(as before); bare prompt filenames use flow/common lookup. `prompts.activities:
null` disables activity selection, and `prompts.outpainting: null` disables the
extra outpainting instructions. Leave a section out to use the flow defaults.
The repository's launch YAMLs contain connection, character, canvas and output
settings; prompt text and workflow bindings live with the flows.

`viking_duo` and `baby_dragon` remain accepted aliases. Old flat YAMLs and saved
session snapshots continue to load without adopting new flow defaults. Missing
paths to the former shipped prompt/workflow files redirect to their new packaged
locations; existing custom files take precedence. Legacy inline outpainting text
is still read for compatibility. Every new session saves its resolved outpainting
prompt as `inputs/outpainting.md`, including when starting from a legacy inline
config. Resuming reads this saved file, so editing `common/outpainting.md` only
changes new sessions. The flows and their assets are included in the wheel.

### Separate generation and outpainting servers

The dashboard shows three progress bars and logs: prompts, generated images, and
outpainted images. Prompt planning always runs independently. Configure GPU work:

```yaml
generation:
  gpu_mode: parallel  # sequential (default) or parallel
comfy:
  base_url: http://192.168.1.186:8188
comfy_outpainting:
  base_url: http://192.168.1.187:8188
```

`comfy_outpainting: null` reuses the generation server. Secondary settings inherit
primary timeouts, retries, and authentication unless explicitly overridden.
Each server is checked only for its own workflow's required nodes and models.

Sequential mode runs generation → outpainting for each sticker before generating
the next. Parallel mode lets generation get ahead while a second server outpaints.
Identical hosts or overlapping resolved IP addresses **always force sequential
mode**, even with different ports. If distinct IPs cannot be verified, execution
also falls back to sequential mode. The effective mode and reason are logged.

Sessions snapshot both endpoints and the requested mode. Pause and resume retain
prompts, intermediate images, and submitted job IDs, so each stage resumes its
unfinished work. Job IDs are tied to their original endpoint; changing an endpoint
for an already submitted job is rejected. With outpainting disabled, only the
primary ComfyUI server is used.

### Stage outputs, logs, and adding outpainting later

Every session keeps independent stage folders:

| Stage | Results | Logs |
| --- | --- | --- |
| Scene planning | `scene_planning/results/stiker-0000.md` — raw and refined descriptions | `scene_planning/logs/` |
| Image generation | `image_generation/results/stiker-0000.jpg` — original Qwen image | `image_generation/logs/` |
| Outpainting | `outpainting/results/stiker-0000.jpg` — extended image | `outpainting/logs/` |

Stage logs are append-only JSON Lines files, one per sticker, plus `worker.jsonl`
for worker events. They record timestamps, progress, step durations, outcomes,
and errors. Resuming appends to the existing logs. Shared resumable state and
submitted workflow graphs remain under `metadata/`.

Base images are kept regardless of whether outpainting is enabled. To add
outpainting to a paused **or completed** session, configure the desired Flux
workflow, target dimensions and ComfyUI endpoint in your launch YAML, then run:

```powershell
uv run birthday-stickers --config config.yaml --headless --resume 2026-10-14-001 --enable-outpainting
```

This explicitly snapshots the outpainting settings into that session and queues
already-generated images. Saved prompts and Qwen images are reused; originals
are never replaced by extended images. Repeating the command skips completed
outpainting. You can also use the selected-session toggle in the interface.
Disabling outpainting preserves extended images and any submitted Flux job IDs.
If an earlier Flux job is still pending, it is allowed to finish before more
Qwen jobs start; the toggle does not cancel submitted server work.
Re-enabling it reuses completed outputs and reconnects to pending jobs using the
session's existing outpainting settings. Settings from the current profile are
imported only when adding outpainting to a session that has no saved Flux workflow.

Older sessions are supported: their `prompts/`, `intermediate/`, and `final/`
artifacts are copied to the corresponding stage folders when resumed, without
deleting originals or overwriting existing stage files. Historical timing data
remains in metadata; new per-stage logs begin on the next run.

### Negative prompts

`flows/common/negative.md` supplies `border, 3d, incomplete image, framed.` to
both Qwen flows. A flow-local `negative.md` overrides the common file. You can
also set `prompts.negative: ./my-prompts/negative.md`, or use `null` to retain only
the exported workflow's negative prompt. Configured text is appended to existing
Qwen negatives, preserving exclusions such as the solo flow's `realistic`.
New sessions snapshot the file as `inputs/negative.md`; edit that saved file
while stopped to change negatives for future jobs in an existing session.
Submitted jobs keep their original prompts.

Flux receives a separate negative text-conditioning node, but its default CFG
of 1 bypasses negative conditioning in ComfyUI. The program reports this in the
outpainting log display. Borderless, complete, edge-to-edge 2D instructions also
appear in the positive planning/refinement and outpainting prompts. CFG is left
unchanged; increasing it changes rendering behavior and has not been GPU-tested
here. Negative prompts reduce unwanted elements but do not guarantee frame-free
results or repair frames already present in a saved source image.
