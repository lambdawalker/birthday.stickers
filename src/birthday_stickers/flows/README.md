# Workflow bindings

New presets use `scene_preset/qwen.json` and one shared graph builder. It creates
`character_reference_1` through `character_reference_N` nodes dynamically and binds
them to `images.image_1` through `images.image_N` on encoder `459:474`. Text-only
characters consume no slots. The client enforces Qwen's documented ten-reference
limit. All presets share canvas/CFG/seed controls and common Qwen 2.1 outpainting.

The packaged workflows are `scene_preset/qwen.json` and
`common/qwen-outpaint.json`. The CLI copies and patches them in memory, then
saves each submitted graph under `output/metadata`. Change model filenames and
sampler inputs with YAML overrides. Retired flow implementations are removed.

| Stage | Node | Runtime binding |
|---|---|---|
| Qwen (presets) | `character_reference_N` | Referenced characters in YAML order |
| Qwen | `459:474` | Refined prompt directly; image sockets follow referenced character order |
| Qwen | `459:456`, `459:458` | Configured empty latent size, batch 1, sampler seed |
| Qwen | `461` | Core `SaveImage`, using the original decoded image connection |
| Outpainting | `200` | Uploaded intermediate JPEG |
| Outpainting | `112` | Raw scene + outpainting instructions; negative prompt; resolution 0 |
| Outpainting | `202` | Centered padding and feathering |
| Outpainting | `205` | Outpaint v2 LoRA, strength 1 |
| Outpainting | `114`, `207` | Sampler seed and SaveImage prefix |
| Outpainting | `206` | Restore original interior with the padding mask |

The Qwen internal prompt enhancer, its CLIP loader, text preview, seed helper,
switches, and batching helper become unreachable and are removed from the submitted
graph. The supplied outpainting caption branch and 1 MP resizer are omitted: prompts come from the local pipeline and source dimensions stay unchanged. Model loaders, conditioning,
cache, decoding, and sampling settings remain from the exports unless overridden.

Qwen generates a new composition from the configured character sources. The explicit empty
latent makes generation dimensions configurable rather than inheriting the first
reference's aspect ratio. Use a current ComfyUI build with Qwen 2.1 support: core
`KSampler` adapts the empty latent's channel count and spatial downscale using its
model format. Older builds may lack that adaptation. Generation sizes are multiples
of 32; the outpainting working canvas is rounded up to multiples of 32 and cropped locally to the exact target.

Qwen reference resolution is `0` in the supplied export, so large reference images
can cost substantial memory. To resize reference conditioning while retaining the
configured output size, use an override such as:

```yaml
workflows:
  qwen:
    # Keep path/nodes from the supplied config, and edit only overrides:
    overrides:
      "459:474": {resolution: 1024}
```

This is a partial YAML example, not a complete configuration. Runtime-managed
prompts, image paths, padding, seeds, latent dimensions, and output naming take
precedence over overrides. Unknown node IDs/input names fail early.

The CLI uses core SaveImage for predictable `/history` outputs, then converts the
downloads to high-quality local JPEGs. ComfyUI's own output folder still contains
the server-side PNGs, and its input folder retains uploads; this tool does not
delete files on your server.

Sources checked October 14, 2026:

- [ComfyUI Qwen nodes](https://github.com/Comfy-Org/ComfyUI/blob/master/comfy_extras/nodes_qwen.py)
- [Core latent and sampler nodes](https://github.com/Comfy-Org/ComfyUI/blob/master/nodes.py)
- [Empty-latent adaptation](https://github.com/Comfy-Org/ComfyUI/blob/master/comfy/sample.py)
- [ComfyUI HTTP routes](https://docs.comfy.org/development/comfyui-server/comms_routes)

## Qwen outpainting requirements

Converted from the supplied `Qwen-Image-2.1-Outpaint-Core (1).json` editor workflow.
The API graph retains core transparency flattening, padding, LoRA, reference cache,
Qwen encoding, sampling, decoding, alpha splitting, and masked compositing.
It needs these installed model files (override their names under `workflows.flux.overrides`):

- `1.unet_name`: `qwen_image_2.1_int8_convrot.safetensors`
- `2.clip_name`: `qwen3vl_8b_int8_convrot.safetensors`
- `3.vae_name`: `qwen_image_2.1_vae_bf16.safetensors`
- `205.lora_name`: `qwen-image-2.1-outpaint-v2.safetensors`

Sampler `114` defaults to 25 steps, CFG 1, Euler, Simple, denoise 1.
The full canvas is sampled without a latent noise mask. Only the decoded result
is masked into the padded source. The historical `workflows.flux` key and job
metadata names remain to avoid changing scheduling and resume bookkeeping; the
packaged graph and user-facing render label now use Qwen. Use new sessions for
this replacement: existing sessions keep their own workflow snapshots and are
not migrated automatically. No model files are downloaded by the application.
