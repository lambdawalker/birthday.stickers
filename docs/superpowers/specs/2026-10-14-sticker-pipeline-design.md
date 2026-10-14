# Birthday sticker pipeline

The requested deliverable is a Python CLI in this repository. Repeat N times:
read a local planning prompt, ask an OpenAI-compatible LLM for a raw scene,
compose a refinement request locally with that response and the Qwen guide,
ask the same LLM for a refined edit prompt, render through ComfyUI Qwen with
the protagonist in image_1 and creature in image_2, save an intermediate JPEG,
outpaint using the original raw scene, and save a final JPEG at a configured size.
Save both scene descriptions together in a Markdown file. Use `stiker-0000.jpg`
spelling and increasing IDs, without overwriting earlier work.

## Implementation decisions

- Python 3.11+, uv, Pillow, PyYAML, and standard-library HTTP client. Sequential
  execution limits GPU pressure and makes per-image failures easy to diagnose.
- YAML contains URLs (IP + port), model, optional API-key environment-variable
  names, timeouts, count, image paths, sizes, seeds, JPEG settings, workflow paths,
  explicit node mappings, and model/sampler input overrides. Paths resolve against
  the YAML location. An optional vision mode sends reference images to the LLM;
  text-only mode never pretends the LLM inspected them.
- Ship the supplied API-format workflows unchanged. Patch deep copies at runtime.
  Qwen encoder 459:474 receives refined text directly; image nodes 514 and 516
  retain their order. Use the explicit empty latent for configured dimensions.
  Replace advanced saving with core SaveImage and prune unreachable nodes, avoiding
  the internal enhancer, its model, and UI preview dependencies.
- Flux loads the generated intermediate through node 47, receives the raw scene
  at node 23, and uses node 44 for padding. Enable the inpaint noise mask. Preserve
  the existing image in the saved result with a feathered central composite.
- Determine padding from decoded intermediate dimensions. Work canvas dimensions
  round up to multiples of 16 for latent processing, then crop only added margins
  to the exact requested size. Odd extra pixels go right/bottom. Reject shrinking
  or stretching. If target equals source, save it without a needless diffusion job.
- Upload images with unique names; submit once; persist prompt IDs; poll per-prompt
  history with bounded retries for GETs only. Do not retry ambiguous submissions.
  Stop on failure with useful node/status information; keep raw/refined prompts,
  intermediate images, graph snapshots, seeds, and metadata for diagnosis.
- Number allocation reserves a metadata file exclusively, so subsequent invocations
  append safely. A failed slot stays reserved. No automatic resume in this version.

## Verification and limits

Unit tests cover workflow graph reachability, reference order, seed/prompt routing,
padding/alignment/cropping, configuration errors, and JPEG conversion. A local HTTP
fixture exercises the real two-call LLM and two-job ComfyUI sequence, error handling,
GET recovery, timeouts, output selection, file naming, and prompt persistence.
CI checks Windows and Linux. Live GPU generation requires the user's actual URLs,
models, custom nodes, and character images, which are not present in the repository.
