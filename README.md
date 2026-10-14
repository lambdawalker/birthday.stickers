# Birthday Stickers

Generate a batch of illustrated scenes with two consistent character references.
An OpenAI-compatible LLM plans each scene and refines it with a local Qwen guide;
ComfyUI renders it with Qwen 2.1 and extends it with Flux Fill to your final size.

For each of N iterations:

1. Read `prompts/plan_scene.md` and ask LLM A for a raw scene.
2. Assemble the raw scene, local Qwen guide, and refinement template locally; ask
   the same LLM for the refined Qwen editing instruction.
3. Upload the protagonist to Qwen input 1 and the fantastic creature to input 2.
4. Run the Qwen workflow and save `output/intermediate/stiker-0000.jpg`.
5. Upload that JPEG, compute centered padding, and run Flux with the **raw** scene.
6. Save `output/final/stiker-0000.jpg` at the exact configured width and height.
7. Keep both descriptions in `output/prompts/stiker-0000.md`, plus job metadata
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

# Start with one image, then run the configured batch:
uv run birthday-stickers --config config.local.yaml --count 1
uv run birthday-stickers --config config.local.yaml
```

Linux/macOS use the same commands, with `cp config.yaml config.local.yaml`.
Python 3.11+ is supported. No CUDA/PyTorch installation is needed on the client;
the LLM and ComfyUI servers run the models.

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
| `generation.count` | Number of new scenes for this invocation |
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
claim of optimal model parameters. See [workflow details](workflows/README.md) for
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

Edit [the scene brief](prompts/plan_scene.md) to set theme, medium, activities,
subjects, or any requested lettering. Its optional placeholders are
`{{iteration}}`, `{{count}}`, `{{sticker_id}}`, and `{{previous_scenes}}`. The last
placeholder includes up to ten earlier scenes from the current invocation to
encourage variety; it is not a guarantee against duplicate compositions.

The [refinement template](prompts/refine_scene.md) must contain `{{guide}}` and
`{{scene}}`. Both are substituted locally in one pass. The raw description is
sent unchanged to Flux's positive prompt node; it is never replaced by Qwen's
reference-tagged refinement.

The [supplied guide](docs/Qwen_Image_2_1_Ultimate_Prompt_Guide.md) is included with
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
| `output/intermediate/` | `stiker-0000.jpg`, `stiker-0001.jpg`, … |
| `output/final/` | Matching JPEG names at the requested dimensions |
| `output/prompts/` | One matching Markdown file with raw and refined descriptions |
| `output/metadata/` | Status, seeds, reference hashes, padding, prompt IDs, submitted JSON graphs |

The requested spelling **`stiker`** is intentional. IDs start at zero and continue
after existing output files. Failed items retain their IDs and partial artifacts;
later invocations append new IDs. There is no automatic resume/retry of incomplete
items in this version. Do not delete the metadata if you need to inspect a failed job.

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
- A failure stops the batch and returns a nonzero exit code. Previously completed
  scenes, prompt files, and intermediates remain on disk.

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

`prompts.activities` in YAML points to `prompts/activities.json`: 300 editable
activities in 12 categories, each with compatible settings. For each scene the
program samples an unused activity, avoids the preceding category when possible,
and selects a setting, mood, framing, and pastel palette. The LLM supplies the
specific poses and delightful details. The sampler is independent of diffusion
seeds. Set `prompts.activities: null` (or omit it) to use only your planning prompt.

Selection history lives in the per-sticker JSON files under `output/metadata`.
It persists across runs using the same output directory. Every activity is used
before a new cycle starts; failed attempts also consume their chosen activity.
The selected brief is saved before the LLM request and appears in the prompt
Markdown alongside the raw and refined descriptions. Keep metadata to retain
history; use a new output directory to start a fresh collection. Run only one
process per output directory when using activity sampling.

Catalog IDs must be unique and stable. Each record needs `id`, `category`,
`activity`, and a nonempty `settings` list. Editing the catalog recalculates history
against the currently available IDs; adding activities makes them available in the
current cycle. Reference images remain local and are not embedded in the catalog.
