# Workflow bindings

These files are the user's original ComfyUI API exports, copied unchanged:

- `multi_character_reference/qwen.json` from `Qwen 2.1 Edit.json`
- `common/flux-outpaint.json` from `Flux outpaint.json`
- `single_character_text/qwen.json` from `Qwen image 2.1.json`

The CLI deep-copies and patches them in memory, then saves each submitted graph
under `output/metadata`. Change model filenames/sampler inputs with YAML overrides.

| Stage | Node | Runtime binding |
|---|---|---|
| Qwen | `514`, `516` | Uploaded protagonist and creature, in that order |
| Qwen | `459:474` | Refined prompt directly; image sockets 1 and 2 remain explicit |
| Qwen | `459:456`, `459:458` | Configured empty latent size, batch 1, sampler seed |
| Qwen | `461` | Core `SaveImage`, using the original decoded image connection |
| Flux | `47` | Uploaded intermediate JPEG |
| Flux | `23` | Original raw scene plus the resolved outpainting prompt |
| Flux | `44` | Computed left/top/right/bottom padding and configured feathering |
| Flux | `38` | Enable `noise_mask` to constrain inpainting |
| Flux | `3`, `9` | Sampler seed and output filename prefix |

The Qwen internal prompt enhancer, its CLIP loader, text preview, seed helper,
switches, and batching helper become unreachable and are removed from the submitted
graph. Flux's disconnected loader `17` is also removed. Model loaders, conditioning,
cache, decoding, and sampling settings remain from the exports unless overridden.

Qwen generates a new composition from two identity sources. The explicit empty
latent makes generation dimensions configurable rather than inheriting the first
reference's aspect ratio. Use a current ComfyUI build with Qwen 2.1 support: core
`KSampler` adapts the empty latent's channel count and spatial downscale using its
model format. Older builds may lack that adaptation. Generation sizes are multiples
of 32; Flux's working canvas is a multiple of 16.

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

The `protagonist_reference_creature_text` flow derives its Qwen graph from the
multi-character export, removes the second image loader/socket and unused prompt
enhancer branch, and binds only node `514` to the protagonist. The dragon is
described in text. It uses the same latent, sampler, output, and outpainting nodes.
