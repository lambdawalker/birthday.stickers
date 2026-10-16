# Birthday Stickers

Generate illustrated scenes from configurable characters, activities, venues and props.
An OpenAI-compatible LLM plans each scene and refines it with a local Qwen guide;
ComfyUI renders it with Qwen 2.1 and extends it with Qwen 2.1 outpainting to your final size.

The default command opens a Textual session picker and live generation dashboard.
Each session has its own folder under `output/sessions/YYYY-MM-DD-NNN/`.

Three workers form a pipeline: the LLM prepares prompts for upcoming scenes while
ComfyUI renders earlier ones. For each of N scenes (paths below are relative to
that session folder):

1. Assemble the selected scene preset locally and ask LLM A for a raw scene.
2. Assemble the raw scene, local Qwen guide, and refinement template locally; ask
   the same LLM for the refined Qwen editing instruction.
3. Upload referenced characters to consecutive Qwen inputs in character-list order.
4. Run the Qwen workflow and save `image_generation/results/stiker-0000.jpg`.
5. Upload that JPEG, compute centered padding, and run Qwen outpainting with the **raw** scene.
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
`B000.jpg`, `B001.jpg`, etc. The shipped duo uses prefixes A and B;
custom presets can use longer prefixes such as `greyBabyDragon`. The suffix is numeric. These images are not yet included in the repo.

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
intermediate upload, Qwen outpainting rendering/download, and final compositing/saving.
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
| `preset` | Packaged preset name or folder path; defines the characters and scene templates |
| `generation.count` | Default total stickers for a new session; editable in the interface |
| `output.directory` | Parent folder containing the `sessions/` directory |
| `generation.width`, `generation.height` | Qwen output canvas; multiples of 32 |
| `output.width`, `output.height` | Exact final width M and height N in pixels |
| `output.jpeg_quality` | Default 95; lower values save more space |
| `output.jpeg_subsampling` | Default 0 (4:4:4) for illustration edges; 2 makes smaller files |
| `output.feathering` | Blended boundary between the original and generated extension |
| `output.preserve_center` | Restore the original center with edge feathering; default true |
| `generation.seed` | `null` for random; integer for seed + 2 × sticker index, then +1 for Qwen outpainting |
| `llm.send_reference_images` | Send the ordered reference images to a vision-capable LLM |
| `llm.extra_body` | Server-specific OpenAI request fields, such as thinking controls |
| `workflows.*.overrides` | Override existing node inputs such as checkpoints, steps, or guidance |

The checked-in defaults preserve the supplied samplers: Qwen 21 steps / CFG 1.4;
Qwen outpainting 25 steps / CFG 1, Euler / Simple / denoise 1. They are workflow settings, not a
claim of optimal model parameters. See [workflow details](src/birthday_stickers/flows/README.md) for
all node bindings and reference-resolution tuning.

Character references are discovered from `characters.directory`, using each
entry's `imagePrefix` in the preset's `characters.yaml`. Selected files stay fixed
through planning, refinement and rendering; paths and hashes are saved in metadata.
See [scene presets](#scene-presets) for the full schema and examples. Legacy role
labels and explicit file paths continue to work for old flow configurations.

An LLM with image input support can inspect the selected references during planning and
refinement. With the default `send_reference_images: false`, it receives role
descriptions only; Qwen still receives all selected full-size reference files.
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

Edit the selected preset's files to change style, characters, activities,
venues and composition. The assembled brief is saved before the LLM request, with
up to ten recent scenes appended to encourage variety.

The shared [refinement template](src/birthday_stickers/flows/common/refinement_master.md)
and [Qwen guide](src/birthday_stickers/flows/scene_preset/guide.md) preserve the
selected character-source assignments and scene details. Refinement uses the raw
response plus the guide, followed by explicit reference-tag assignments. The raw
scene remains the input to Qwen outpainting; it is never replaced by the reference-tagged
Qwen refinement. The graph's internal prompt enhancer is bypassed.

### Master planning and refinement templates

Each preset has its own editable `planning_master.md` and `refinement_master.md`
in `src/birthday_stickers/presets/<preset-name>/`. Edit these to customize only
that preset. Switching presets selects its own templates. A custom preset missing
either file falls back to that file in `src/birthday_stickers/flows/common/`.
An explicit `prompts.planning` or `prompts.refinement` YAML override takes priority
and is retained when switching presets.

Planning inserts `{{ scene_brief }}` (required), which contains the rendered image
style, character blocks and general direction in that order. Optional variables
are `previous_scenes`, `iteration`, `count` and `sticker_id`. Refinement requires
`{{ guide }}`, `{{ scene }}` and `{{ character_sources }}`; the last contains the
exact ID/name-to-image-tag assignments and descriptions. Both use strict Jinja.
Inserted LLM responses and preset text are data, not evaluated again as templates.

To customize without editing packaged files, copy these two files and configure:

```yaml
prompts:
  planning: ./my-prompts/planning_master.md
  refinement: ./my-prompts/refinement_master.md
```

Switching presets keeps these explicit overrides. New sessions snapshot the resolved files into
`inputs/planning.md` and `inputs/refinement.md`. The rendered `planning_request`
and `refinement_request` are saved in scene metadata before their first LLM call.
`--preview-prompt` now shows the final master planning request. Invalid template
variables or missing required placeholders are reported before contacting services.

If Qwen refinement omits a reference tag or invents an extra one, the planner sends
the rejected answer back with the exact missing/unexpected tags and asks for a
corrected scene. The raw scene, references and seeds are reused. Rejected answers
are logged in `scene_planning/logs/`; the latest rejection is checkpointed in
metadata until corrected. Retries wait 1, 2, 3, ... up to 15 seconds, then continue
at 15 seconds. Pause/Stop interrupts retry waits and resume continues correction.
Only a valid refinement is handed to image generation. Persistent mistakes remain
visible in the logs so you can stop and adjust the LLM or master template.

## Dimensions and output files

For source width W and requested width M, the desired left padding is
`(M - W) // 2`, with the remainder on the right. Height works the same way.
The script measures the actual intermediate JPEG before calculating this.

Qwen outpainting gets a working canvas rounded up to multiples of 32. After generation,
the script crops only the additional outer margins to deliver the exact target.
For example, a 1024 × 1024 intermediate into 1600 × 1200 needs 288 px on each
horizontal side and 88 px on each vertical side. Odd remainders go right/bottom.
The central image is never stretched or cropped. A target smaller than the source
is rejected. An equal-size target skips the redundant Qwen outpainting job.

The default central composite protects the original subjects from diffusion/VAE
changes, while feathering allows a smooth border. JPEG compression means the saved
pixels are not bit-for-bit identical. Turn `preserve_center` off to retain Qwen outpainting's
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
- Transient I/O retries indefinitely with delays from 1 to 15 seconds. Ambiguous
  Comfy submissions recover by request ID instead of creating duplicate jobs.
- Polling uses `/history/{prompt_id}` and retrieves only the configured output node.
  It reports execution errors and keeps waiting for queued/running jobs until completion
  or an explicit Pause/Stop.
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

## Scene presets

All new generation types use `flow: scene_preset`. Select a preset on the first
screen, or set `preset` in your launch YAML. The three previous setups have been
translated into packaged folders under `src/birthday_stickers/presets/`:

| Preset | Characters |
| --- | --- |
| `girl_dragon_references` | Girl from A references, dragon from B references |
| `baby_dragon_text` | One baby dragon described in text |
| `girl_reference_dragon_text` | Girl from A references, baby dragon described in text |

Each retains a 300-activity catalog and Viking art direction. Every preset uses
the same three pipelines, session controls, CFG/resolution fields, negative
prompt, and optional outpainting. Switching presets keeps the launcher's service
URLs, output settings, workflow overrides and outpainting setting. Existing
sessions resume their saved settings regardless of the chosen new-session preset.
Session format 2 is required. Earlier sessions are not resumable; their files remain on disk.

Copy one packaged preset into `presets/my_story/` beside your launch YAML to
customize it. The interface discovers folders there automatically; a local folder
with the same name overrides its packaged counterpart. A preset folder contains:

| File | Purpose |
| --- | --- |
| `characters.yaml` | Stable character IDs, names, optional descriptions/reference prefixes |
| `activities.yaml` | Named activities and their local props |
| `venues.yaml` | Named venues and their local foreground/background props |
| `props.yaml` | Global props for styling and general direction |
| `image_style.md` | Medium, palette and visual style; first in the planning prompt |
| `general_direction.md` | Environment, placement, activity and composition template |
| `planning_master.md` | Master template for the initial LLM request |
| `refinement_master.md` | Master template for the Qwen refinement request |

```yaml
# Launch YAML
flow: scene_preset
preset: presets/my_story  # relative to this YAML; packaged names also work
characters:
  directory: assets/characters
```

`characters.yaml`:

```yaml
characters:
  - id: dragon
    name: baby dragon
    description: Grey dragon with small wings and a rounded belly.
    imagePrefix: greyBabyDragon
  - id: girl
    name: princess
    description: Baby girl in a cozy Viking outfit.
    imagePrefix: A
```

At least one of `description` or `imagePrefix` is required. Description only means
text-to-image for that character; prefix only means reference identity; both means
reference identity plus supplementary instructions. IDs start with a letter and
contain letters, digits or underscores. Use `characters.dragon.name` in templates,
so reordering the list does not break references. Names and descriptions are plain
text, not recursively evaluated templates.

Reference filenames match the exact, case-sensitive prefix plus any number of
digits, with case-insensitive `.jpg`: `greyBabyDragon0.jpg`, `greyBabyDragon1234.JPG`,
`A23.jpg`. Supported non-JPG sources are converted automatically, preserving
originals and resolving collisions. One version is independently chosen at random
per referenced character for each new scene. Prefixes that match the same filename
(e.g. `A` and `A1`) are rejected. Referenced entries get consecutive Qwen image
slots in YAML order; text-only entries do not consume slots.

[Qwen Image 2.1](https://github.com/QwenLM/Qwen-Image-2.1) documents up to **10
reference images**. That is the enforced reference limit, not a documented maximum
number of text-described characters. More subjects can still reduce visual
consistency. The graph adds only the required image nodes and skips all reference
uploads for a text-only preset. Your ComfyUI installation must support the Qwen
2.1 encoder and its dynamic image inputs.

`activities.yaml`:

```yaml
activities:
  - id: baking
    name: Baking
    description: >-
      {{ characters.dragon.name }} stirs with {{ props.spoons | random }}
      while {{ characters.girl.name }} holds {{ props.pots | random }}.
    requiredCharacters: [dragon, girl]
    venueTags: [kitchen]
    props:
      spoons: [a big wooden spoon, a small wooden spoon]
      pots: [a copper pot, a new clay pot]
```

`venues.yaml`:

```yaml
venues:
  - id: longhouse_kitchen
    name: Longhouse kitchen
    description: A cozy Viking longhouse kitchen with carved timber walls.
    tags: [kitchen, indoor]
    props:
      foreground: [a woven rug, a wooden bowl]
      background: [a shelf of clay pots, a small window]
```

`props.yaml`:

```yaml
props:
  skyProp: [small cloud, pale crescent moon]
  palettes: [blush pink and lavender, powder blue and pale butter yellow]
```

`image_style.md`:

```jinja
A soft 2D storybook illustration in {{ props.palettes | random }}.
Complete, edge-to-edge scenery with no frame or border.
```

`general_direction.md`:

```jinja
{{ activity.description }}
Set the scene in {{ venue.description }}.
Place {{ venue.props.foreground | random }} in the foreground.
Place {{ venue.props.background | random }} in the background.
A {{ props.skyProp | random }} is in the right corner, visible through a window.
Keep both characters centered with complete bodies and clear shared interaction.
```

The assembled prompt order is **image style → character blocks → general
direction**. Activity and venue descriptions are rendered first so the final
direction can include them. Inside each activity/venue description, `props` refers
to that entry's own props. In style/direction, `props` refers to `props.yaml`.
`globalProps` always accesses global props; `venue.props` accesses the chosen
venue's props; `activity.props` accesses the chosen activity's props in the final
templates. Each `| random` invocation makes a choice. To reuse the same choice
within one template, bind it once: `{% set spoon = props.spoons | random %}`.
Prop values are literal data, not recursively rendered templates.

Activities and venues need a name and description. An explicit stable `id` is
recommended; otherwise one is derived from the name. Activities with missing
`requiredCharacters` are excluded. `venueTags` matches any listed venue tag; omit
it to allow all venues. Activities are sampled without replacement until all
eligible entries have been used, then a new cycle begins. Venue and prop choices
are random, independently of diffusion seeds. Templates use sandboxed Jinja with
strict undefined-variable errors. Invalid IDs, missing variables, empty random
lists, and activities with no compatible venues fail before LLM/Comfy work.

The rendered brief, selections, seed, reference paths and hashes are saved before
calling the LLM. A retry/resume reuses those choices. The Markdown scene artifact
contains the assembled request plus the raw and refined responses. Sessions copy
the preset files and its own master templates; they keep references in their live source directory.
New reference files become available to unplanned scenes, including after resume.
Already-planned scenes keep their selected reference files and hashes.

Preview a locally assembled planning prompt without contacting model servers:

```sh
uv run birthday-stickers --config config.dragon.yaml --preview-prompt --preview-seed 42
uv run birthday-stickers --config config.local.yaml --preset presets/my_story --preview-prompt
```

A preview using references requires matching local files. The same preview seed
and unchanged inputs reproduce the choices. `--dry-run` additionally validates
image files, graph bindings and dimensions; `--check` also checks the services.

### Optional outpainting

The **Outpainting** switch beside **New session** controls new sessions.
**Change preset** is below those controls. A separate **Outpainting** switch beside
**Resume selected** follows the highlighted saved-session row, including completed
sessions. Change it and press **Resume selected** to apply the setting
and resume. Switching rows discards an unapplied toggle change.
`generation.outpainting: true/false` sets the initial switch value and controls
headless runs. The duo default is on; the solo dragon default is off.

With outpainting on, any scene preset saves an intermediate JPEG, then uses the
original raw scene prompt with Qwen outpainting. Padding centers the source in the configured
final canvas. With it off, Qwen saves to `image_generation/results/` and Qwen outpainting is
not validated, uploaded to, or run. Existing sessions without this setting retain
the old behavior: duo on, solo dragon off.

All presets inherit `workflows.flux` from `flows/common/flow.yaml`. For an older
flat configuration, migrate to `flow: scene_preset` with a `preset` folder and remove its
`prompts`/`workflows` sections to use these defaults. Keep custom model overrides
and choose larger `output.width/height` before enabling the switch.
The switch is saved in the session snapshot, along with the chosen dimensions.


### Avoiding foggy outpaint borders

The shared `flows/common/flow.yaml` now uses the supplied core Qwen 2.1 outpainting
workflow with the outpaint v2 LoRA: 25 steps / CFG 1 / Euler / Simple / denoise 1.
It samples the full padded canvas, then restores the original interior using the
padding mask. Encoder resolution 0 preserves canvas geometry. The working canvas
is rounded to multiples of 32, with the final JPEG cropped to the configured size.

The resolved `outpainting.md` file appends local continuity instructions to the **original
raw scene**, requesting consistent detail, lighting and focus rather than a vignette
or blurred frame. Added regions must contain scenery only, with no new characters
or duplicated faces/bodies; an existing character crossing the boundary may be
continued anatomically. The exact combined prompt is saved as `outpainting_prompt` in
scene metadata and in the submitted Qwen outpainting graph. Set `prompts.outpainting: null`
for raw-only behavior in a flow-based config.
The 24-pixel feather and center preservation remain unchanged: they only blend the
source boundary, and do not blur the entire generated border.

Flow-based configs inherit these defaults. Existing session snapshots
and already-submitted jobs retain their previous settings. Compare new sessions with
the same scene/seed before deciding whether the new settings improve your output.
For a paused session, changes to its saved YAML only affect Qwen outpainting jobs not yet submitted;
never clear a saved job ID to rerender a job that may still be running.


### Flow module and prompt lookup

The shared Qwen builder and refinement assets live in
`src/birthday_stickers/flows/scene_preset/`. Scene content lives in
`src/birthday_stickers/presets/`; common outpainting/negative prompts and Qwen outpainting
bindings live in `flows/common/`. Only preset-based configurations are supported;
retired flow folders and compatibility aliases have been removed.

Prompt filenames are searched **in the selected flow folder first, then in
`flows/common/`**. For example, adding `scene_preset/outpainting.md`
overrides the shared version only for that flow. A missing file in both locations
produces an error naming both searched directories. The same resolver is used for
default workflow assets. Activity catalogs live in the preset folder.

Optional config overrides still work:

```yaml
prompts:
  planning: ./my-prompts/planning.md  # explicit path, relative to this YAML
  outpainting: outpainting.md       # flow folder, then common
workflows:
  flux:
    overrides:
      "114": {steps: 40}              # merged with the flow's workflow defaults
```

Absolute paths and paths containing directories resolve relative to the YAML
and bare prompt filenames use flow/common lookup. Presets select activities from
their own catalog. `prompts.outpainting: null` disables extra outpainting
instructions. Leave a section out to use the flow defaults.

Every launch configuration must specify `preset`. The three root `config*.yaml`
files remain supported because they select current presets. Old flow names,
legacy character-role mappings, JSON activity catalogs, inline
`output.outpainting_prompt`, and redirects from former asset paths are no longer
supported. Put outpainting instructions in a Markdown file instead.

Every new session saves its resolved outpainting prompt as `inputs/outpainting.md`.
Resuming reads that saved file, so editing `common/outpainting.md` only changes
new sessions. The flows and their assets are included in the wheel.

### Separate generation and outpainting servers

The dashboard shows three progress bars and logs: prompts, generated images, and
outpainted images. Prompt planning always runs independently. Configure GPU work:

```yaml
generation:
  gpu_mode: parallel  # sequential (default), concurrent, or parallel
comfy:
  base_url: http://192.168.1.186:8188
comfy_outpainting:
  base_url: http://192.168.1.187:8188
```

`comfy_outpainting: null` reuses the generation server. Secondary settings inherit
primary timeouts, retries, and authentication unless explicitly overridden.
Each server is checked only for its own workflow's required nodes and models.

Sequential mode completes the entire image-generation batch before outpainting
starts. Concurrent mode alternates generation → outpainting for each sticker,
without overlapping GPU jobs. Parallel mode lets generation get ahead while a
second server outpaints.
When parallel is requested, identical hosts or overlapping resolved IP addresses
**force batch-first sequential mode**, even with different ports. If distinct IPs cannot be verified, execution
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
outpainting to a paused **or completed** session, configure the desired Qwen outpainting
workflow, target dimensions and ComfyUI endpoint in your launch YAML, then run:

```powershell
uv run birthday-stickers --config config.yaml --headless --resume 2026-10-14-001 --enable-outpainting
```

This explicitly snapshots the outpainting settings into that session and queues
already-generated images. Saved prompts and Qwen images are reused; originals
are never replaced by extended images. Repeating the command skips completed
outpainting. You can also use the selected-session toggle in the interface.
Disabling outpainting preserves extended images and any submitted Qwen outpainting job IDs.
If an earlier Qwen outpainting job is still pending, it is allowed to finish before more
Qwen jobs start; the toggle does not cancel submitted server work.
Re-enabling it reuses completed outputs and reconnects to pending jobs using the
session's existing outpainting settings. Settings from the current profile are
imported only when adding outpainting to a session that has no saved Qwen outpainting workflow.

New sessions use manifest version **2**. Sessions from before the master-template
update are unsupported and excluded from the resumable session table, with an
explanation shown in the interface. Headless resume reports the same error. Start
a new session after updating. Existing output folders are retained; the program
no longer imports or copies the old `prompts/`, `intermediate/`, or `final/` layout.

### Negative prompts

`flows/common/negative.md` supplies `border, 3d, incomplete image, framed.` to
the shared Qwen flow. A flow-local `negative.md` overrides the common file. You can
also set `prompts.negative: ./my-prompts/negative.md`, or use `null` to retain only
the exported workflow's negative prompt. Configured text is appended to existing
Qwen negatives, preserving exclusions such as the solo flow's `realistic`.
New sessions snapshot the file as `inputs/negative.md`; edit that saved file
while stopped to change negatives for future jobs in an existing session.
Submitted jobs keep their original prompts.

Qwen outpainting receives negatives through TextEncodeQwenImage21, but its default CFG
of 1 bypasses negative conditioning in ComfyUI. The program reports this in the
outpainting log display. Borderless, complete, edge-to-edge 2D instructions also
appear in the positive planning/refinement and outpainting prompts. CFG is left
unchanged; increasing it changes rendering behavior and has not been GPU-tested
here. Negative prompts reduce unwanted elements but do not guarantee frame-free
results or repair frames already present in a saved source image.

### Regenerate selected images by deleting their files

Pause or stop the session before deleting files. Review the images in your usual
image viewer or file manager, delete unwanted results, then select the saved
session and press **Resume selected** (completed sessions are listed too).

| Deleted result | Work repeated on resume |
| --- | --- |
| `image_generation/results/stiker-NNNN.jpg` | Generate a new base image and invalidate its outpainting |
| `outpainting/results/stiker-NNNN.jpg` | Outpaint the existing base image again |
| Both images | Generate the base, then outpaint if enabled |

Each invalidated stage gets a new seed, different from its preceding seed, even
when the initial batch used a configured fixed seed. The saved raw/refined
prompts and selected character references are reused. Other stickers are untouched.
Reference files must still exist and match their recorded hashes.

When a base image is deleted, its existing outpainted image moves to
`outpainting/superseded/` before regeneration. This prevents outdated derived images
from remaining in `results/`. If outpainting is disabled, it remains pending until
re-enabled. Deleted files themselves cannot be recovered by this process.

The program distinguishes deleted completed results from outputs that have never
finished: pending jobs still resume with their original IDs and seeds. If a
base image is removed while its outpainting job is pending, that job's identity
is retained and its completion is checked before replacement work starts.
Unknown submission outcomes still require resolution rather than duplicate jobs.
Regeneration history (old seeds/IDs and new seeds) is saved in sticker metadata,
and the affected stage's log records the regeneration request. Repeated resume
attempts reuse the newly chosen seeds until another saved output is deleted.

Only missing image outputs trigger regeneration. Deleting prompt Markdown files,
logs, or metadata is not a regeneration control; retain those files.

### Standalone dragon-centering tool

`birthday-center-dragon` detects the main black subject, saves a bounding-box
preview and mask, and crops around its center. It runs independently from the
pipeline, using Pillow only:

```powershell
uv run birthday-center-dragon "path/to/image.jpg" --padding 0.15 --size 1024 1024
```

See [standalone image tools](docs/center_dragon.md) for tuning and limitations.

For **Girl + dragon**, the session picker shows the live number of reference
versions per character. Use **Refresh** after adding reference images. The new-session
panel accepts Qwen CFG and generation width/height (multiples of 32). With
outpainting enabled, these dimensions must fit within the configured final size.
These values are saved with the session; selecting an existing session shows its
saved resolution and CFG. The image-generation panel displays the selected
reference filenames for the current sticker.

`generation.cfg` optionally overrides the multi-character Qwen sampler's CFG.
Omit it or use `null` to retain the workflow/override value.


### Recovery from disconnections and locked files

Transient network errors and file-sharing/permission locks retry indefinitely with
waits of **1, 2, 3, …, 15 seconds**, then 15 seconds for each later retry. A successful
operation resets the delay. Retry messages appear in the worker logs. Pause/Stop
interrupts retry waits; an HTTP request already in flight is bounded by its request
timeout. Invalid configuration, HTTP authentication/validation errors, missing
required files, and corrupt images still report errors instead of looping forever.

LLM calls, uploads, history reads, and downloads reconnect automatically. ComfyUI
submissions carry a saved request identifier. If a response is lost, the application
looks for that identifier in the queue and history instead of submitting a second
render. Recovery continues across session restarts. If ComfyUI has discarded that
history, recovery keeps waiting; stop and inspect the server before clearing the
saved pending submission. Older pending submissions without identifiers still need
manual resolution. The legacy `get_retries`, `retry_delay`, and `job_timeout` settings
remain accepted for old configurations, but no longer cap retries or job waiting.

Before reference selection, supported non-JPG character files such as `A789.png`
are converted to high-quality JPG with EXIF orientation respected and transparency
flattened on white. If `A789.jpg` exists, a new number above the largest existing
number for A is used, without overwriting it. Original files are retained. Conversion
receipts prevent duplicates on subsequent runs; changing a source creates a new
version, and deleting its converted JPG recreates it while the source remains.
Preset imports use the configured prefix followed by digits. The standalone
`birthday-convert-to-jpg` also handles name collisions without overwriting existing JPGs.

### Packaged JPG converter

The converter implementation is now `src/birthday_stickers/convert_to_jpg.py` and
shares JPEG encoding with automatic character conversion. Run:

```powershell
uv run python -m birthday_stickers.convert_to_jpg "C:\pictures"
uv run birthday-convert-to-jpg "C:\pictures" --keep-originals
```

Root-level script launchers have been removed; use the installed commands above.
As before, the standalone converter deletes originals after successful conversion
unless `--keep-originals` is supplied; automatic character conversion retains them.


## Gallery export and review

In the terminal session screen, **Generate gallery** copies images from all sessions
into `gallery/` beside the launch configuration. Originals stay in their sessions.
Each pipeline has its own flat folder: `gallery/image_generation/` and
`gallery/outpainting/`. Files are named by the SHA-256 of their bytes, so repeated
exports and duplicate images across sessions do not create duplicate gallery files.
`gallery/gallery.sqlite3` is the persistent manifest: it records each image, every
source path, reviewers, decisions, and Undo history. Back up the entire gallery folder.

If the original session folders were deleted and you already have a flat collection:

```powershell
uv run birthday-gallery-fix "D:\pictures\flat-collection"
```

This standalone import needs no generation config or session metadata. It copies
supported images directly from that folder, skips existing hashes, retains originals,
and reports corrupt files without abandoning the rest. Use `--gallery PATH` to change
the destination, or `--pipeline outpainting` for that pipeline. Run it again whenever
new images arrive. Different byte encodings of the same picture are distinct hashes.

Start the review website separately (it can run while images are being generated):

```powershell
uv run birthday-review --gallery ./gallery --host 0.0.0.0 --port 8080
```

Open `http://<computer-ip>:8080/user/maria`, replacing the name for each reviewer.
On Windows, allow the server through the firewall on your private network if prompted.
The website defaults directly to image generation. Pipeline-aware storage and URLs
already support `?pipeline=outpainting`; a visible selector can be added later.

- Right / Accept: accept and advance.
- Left / Reject: reject and advance, clearing favorite and T-shirt marks.
- Up / star: toggle favorite, accept, and advance.
- Down / T-shirt: toggle T-shirt candidate, accept, and advance.
- Undo: restore the previous decision, including both marks.

The star and T-shirt controls sit at the bottom center. Arrow keys work on desktop;
`z` undoes the last action. Category links show accepted, rejected, favorite, and
T-shirt galleries. **Review sessions** lists each person's counts and links to their
selections. Each person's decisions are independent. Reopening the same named link
resumes the remaining queue; repeated exports do not reset progress. A failed save
stays available for retry, including after a reload; retried requests are idempotent.

Names in links identify reviewers without passwords. Anyone with a person's link
can continue or change that person's review. This server is intended for a trusted
network. Rejection never deletes an image.

## Packaged utilities and Wi-Fi watchdog

All Python utilities now live under `src/birthday_stickers/`:

```powershell
uv run birthday-convert-to-jpg "D:\pictures" --keep-originals
uv run birthday-center-dragon "D:\pictures\dragon.jpg"
uv run birthday-keep-wifi
```

On Windows, the generation CLI starts `keep_wifi_connected` in a separate child
process for the lifetime of the terminal app or headless generation run. It uses
the current saved Wi-Fi profile, checks every 10 seconds, and logs to
`gallery/logs/wifi.log`. In background mode it chooses the first connected adapter
without prompting. Connect to your desired Wi-Fi before starting. The child stops
when the generation program exits. Use `--no-wifi` to disable it; other operating
systems skip it automatically. Standalone `birthday-keep-wifi` still allows adapter
selection. Preview, validation, and the review server do not start the watchdog.

The destructive flattening utility and its command have been removed. Use
**Generate gallery** or `birthday-gallery-fix` instead.

Review pages preload 3 images ahead and behind by default (including recently reviewed
images for Undo). Set `?preload=5` on a reviewer link to change the buffer to 5
images in each direction; supported values are 1–20. The next image sits underneath
the current card while dragging. Saved choices show a brief confirmation icon;
failed requests retain the retry action without showing a success confirmation.

Use the eye button to switch to gallery mode (`?mode=gallery`). Drag horizontally
to browse a looping gallery; use the shared action buttons to categorize the
visible image. Corner badges show its saved categories. The ↔ button returns
to swipe mode. Both modes retain the selected category and preload setting.

### Qwen 2.1 outpainting workflow

Outpainting now uses `flows/common/qwen-outpaint.json`, converted from the supplied
core-node workflow. Install `qwen-image-2.1-outpaint-v2.safetensors` under ComfyUI's
LoRA directory, alongside the Qwen model, Qwen3-VL encoder and Qwen VAE already used
for generation. The graph does not download models. Model filenames and sampler
settings can be changed through overrides; see the workflow bindings document.

The YAML key `workflows.flux` and existing job metadata names are retained for
bookkeeping, but the default graph is Qwen. Start a **new session** to use this
replacement; existing session snapshots are not automatically converted. Any old
Flux-specific overrides must be removed or remapped to the new node IDs.
The attachment's captioning branch is replaced by the local raw scene plus
`outpainting.md`; its 1 MP resizer is omitted so the source remains at its saved
size. Padding is computed around that source. Masked restoration runs once in
ComfyUI when `output.preserve_center` is enabled; the client then crops and saves
JPEG, without applying feathering a second time.

## Outpaint a folder of existing images

Use the independent command (no scene preset or character references required):

```bash
uv run birthday-outpaint --config outpaint.yaml
```

Edit `outpaint.yaml` with a vision-capable OpenAI-compatible LLM URL/model, ComfyUI
URL, source folder and destination folder. The terminal interface lets you adjust
both folders, final canvas width/height, feathering, sampling steps, CFG and JPEG
quality. Start / Resume, Pause and Stop preserve checkpoints. Ctrl+Q stops at a
checkpoint before exiting. Server-side ComfyUI jobs continue and are recovered on
resume. Separate progress bars track descriptions and finished outpaintings;
logs show service URLs and elapsed time. The Windows Wi-Fi helper runs separately
unless `--no-wifi` is supplied.

```bash
uv run birthday-outpaint --config outpaint.yaml --source ./pictures --destination ./expanded
uv run birthday-outpaint --config outpaint.yaml --headless
```

Each image is sent to the vision LLM with the editable
`flows/common/describe-outpainting.md` prompt. Its description is combined with
`outpainting.md`, then submitted to the Qwen outpainting workflow. You may override
`description_prompt`, `outpainting_prompt` and `negative_prompt` with local files
in YAML. `overrides` controls model filenames and other workflow inputs. The
outpaint v2 LoRA and Qwen model files must already be installed on ComfyUI.

Supported inputs: JPG, PNG, WebP, BMP and TIFF. `recursive: true` includes subfolders;
the destination subtree is excluded. Sources remain untouched. Target dimensions
must be at least as large as each source; smaller targets are reported as failures.
Equal-size images are saved as JPEG without a redundant ComfyUI render.

The destination contains `results/`, `prompts/`, `state/`, `logs/` and `uploads/`.
Output names combine the source stem and a fingerprint of its path, content,
settings and prompts to avoid collisions. Repeating an unchanged run skips finished
images, reuses descriptions and resumes queued ComfyUI jobs. Changing settings,
source content or prompt files creates a distinct result. Keep `state/` to resume.
Only one process may run against a destination folder at a time. Individual image
errors are logged and processing continues with the remaining files.

The main `birthday-stickers` preset picker now uses a keyboard-friendly list:
select with Up/Down and Enter, or click a row.
