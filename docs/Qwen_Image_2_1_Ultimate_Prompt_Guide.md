# The Ultimate Qwen-Image 2.1 Prompting Guide

**Research date:** October 14, 2026  
**Target:** Qwen/Qwen-Image-2.1, Qwen-Image-2.1-Turbo, and Qwen-Image-2.1 hosted variants when noted.  
**Purpose:** A practical, evidence-ranked reference for human prompt writers, automated prompt enhancers, image editors, and agentic image workflows.

**Repository revision, October 14, 2026:** The supplied guide is retained below,
with this automation supplement and targeted clarifications. The official T2I
and edit prompt contracts were checked for this pipeline; other inherited model
and hosted-service claims have not been independently re-audited here.

## Automation supplement: two characters, new scene, then outpainting

### Choose the right prompt contract

The [official T2I rewriter](https://github.com/QwenLM/Qwen-Image-2.1/blob/main/prompt_rewrite/prompts/system_prompt_t2i.txt)
describes a finished image and returns a JSON object with `rewritten_prompt` and
`wh_ratio`. Its expansion style and occasional uncertainty language belong to
that T2I task, not to precise editing instructions.

The [current official edit rewriter](https://github.com/QwenLM/Qwen-Image-2.1/blob/main/prompt_rewrite/prompts/system_prompt_edit.txt)
returns `rewritten_prompt`, `wh_ratio`, and `ratio_follow`. The two ratio fields
are mutually exclusive. It calls for decisive instructions, individual reference
roles, and identity preservation. It explicitly recognizes new-scene tasks in
which all supplied images are identity sources and none is a base canvas.

The embedded enhancer text in the supplied ComfyUI workflow uses a different,
plain-paragraph output contract. Do not confuse that embedded version with the
current upstream JSON contract. A workflow text input needs the actual instruction,
not a serialized response object.

### Project-specific rules (engineering choices, not universal model rules)

For this birthday pipeline, use the two references as follows:

| Input | Role | Preserve | Allow the scene to change |
|---|---|---|---|
| `<image1>` | Protagonist identity | Recognizable face, proportions, hairstyle, outfit/design | Pose, action, placement, viewpoint |
| `<image2>` | Fantastic creature identity | Recognizable face, anatomy, silhouette, colors/design | Pose, action, placement, viewpoint |

Neither reference supplies the base composition. Avoid blanket instructions to
preserve its pose or background: those would conflict with generating new scenes.
Appearance comes from the references; a text-only LLM should not invent a detailed
face description or pretend to inspect images it has not received.

Keep the raw scene rich enough to describe the setting beyond the characters.
That original scene is also Flux's prompt. Keep reference tags out of the raw
description, since Flux receives only the completed intermediate image.

The local refiner receives the guide and raw scene, with an explicit contract to
return one paragraph containing both image tags. Qwen receives that paragraph
directly. Its internal enhancer is bypassed so a second rewrite cannot silently
change the planned scene. If an LLM returns the recognized upstream JSON envelope,
the client extracts `rewritten_prompt` and still takes dimensions from YAML.

These stickers are complete illustrated scenes saved as JPEG. Do not use this
guide's transparent-RGBA wrapper for this pipeline: JPEG cannot preserve alpha.
Avoid adding unrequested lettering; preserve any explicitly requested text exactly.

### Outpainting and identity checks

Outpainting uses the original scene, the intermediate JPEG, and a padding mask.
The client computes geometry from the actual intermediate dimensions, centers it,
and accounts for latent alignment before cropping added margins to the requested
size. Prompt words do not determine output dimensions.

The default final composite restores the existing central image with feathered
edges, reducing the risk that Flux changes the characters. This is an engineering
safeguard, not evidence that either model preserves identity perfectly. Inspect
the first result at full size before running a large batch; check both faces,
creature anatomy, whole silhouettes, border continuity, and JPEG edge quality.

For consistency experiments, keep the saved raw/refined prompts, graphs, reference
hashes, dimensions, and seeds together. A repeated seed alone does not guarantee
the same result after changing server versions, checkpoints, or execution settings.

> **Evidence key:** **Official** = explicitly supported by Qwen/Alibaba documentation or code. **Community** = reported by users, not independently benchmarked. **Heuristic** = reasoned practical method requiring testing. These labels matter: tips applicable to older Qwen-Image checkpoints should not automatically be treated as verified for 2.1.

## Contents

1. Model scope and version differences
2. The two primary prompting modes
3. Official text-to-image rewrite methodology
4. Complete text-to-image prompt framework
5. Official editing methodology
6. Reference images and consistency
7. Composition and visual art direction
8. Typography, infographics, and precise text
9. Transparency, extraction, and asset design
10. Specialized prompt recipes
11. Parameter and workflow notes
12. Troubleshooting and iteration
13. Prompt-enhancer system prompts
14. Quality checklist and evaluation rubric
15. Official and community source library

---

## 1. Model scope and version differences

The **Qwen-Image-2.1** family is not interchangeable with Qwen-Image (2025), Qwen-Image-2512, Qwen-Image-Edit-2509, Qwen-Image-2.0, or the later 3.0 hosted API. Avoid blindly copying pipeline names, inference parameters, negative prompt conventions, or reference-image limits across versions.

**Official confirmed properties of Qwen-Image-2.1:** unified text-to-image and image editing; 7B visual-generation parameters; transparent RGBA outputs; local edits marked with circles, painted annotations, or masks; support for up to ten reference images; native 2K-class image generation; ability to preserve people and products across edits. Qwen released its base open-weights model September 20, 2026 and Turbo October 9, 2026. The Turbo checkpoint uses a model-specific recommended eight-step schedule. Hosted Pro and Turbo APIs also exist. Always verify the specific endpoint's limits separately from the open weights.

**Official warning on pipeline parameters:** The Qwen-Image-2.1 cloud API guide says that the 2.1 series does not support the `negative_prompt` parameter; express exclusions as positive scene constraints. Local Diffusers pipeline signatures and community nodes may differ. Similarly, hosted API size restrictions and the open model's documented recommended dimensions are not the same thing. Do not treat an image size chart from one interface as a universal service quota.

**Model choice:** Choose base when experimenting with model behavior and comparing seeds; choose Turbo when speed is important and use its included schedule, not an arbitrary eight-step setting forced onto the base model. Image editing with references is frequently praised in community reports; some users report that unconditional text-to-image is less reliable than editing. Treat those reports as subjective evidence, not a universal ranking.

## 2. Two modes, two languages of prompting

### Text-to-image: the output is already visible

**Official:** Qwen's T2I prompt-rewriter describes the finished image in English, as an observer, in third-person present tense. Start with the media type, style, and main subject; describe the background, frame regions, people, objects, text, materials, and lighting; finish with a one-sentence statement of visual cohesion. It is *not* a command to an artist.

Weak: `Draw a great 4K dragon poster with cool lighting.`

Strong: `A wide ink-and-alcohol-marker illustration of a charcoal-black friendly dragon gliding through pale blue clouds, with a young child safely seated between its shoulders. The dragon occupies the central-right area; a distant stone castle rises near the lower-left horizon. Smooth marker gradients shape the clouds, while dark confident outlines define the dragon's scales and rounded face. A warm late-afternoon glow from the upper left creates soft highlights on the dragon's head. The composition leaves an uncluttered area across the upper third for invitation lettering and feels playful, airy, and adventurous.`

### Editing: specify a transformation and invariants

**Official:** Qwen's edit rewriter leads with the *operation*, targets observable regions, and explicitly names aspects that must remain unchanged. In a small edit, preserve everything except the targeted attribute. For a new scene built from an existing person or product, actively describe the scene while preserving identity.

Weak: `Make it better and change the background.`

Strong: `Replace only the pale indoor background with an evening castle courtyard. Keep the child and dragon from the input image identical in facial features, body shape, pose, scale, clothing, and silhouette. Preserve the original illustrated ink-and-marker rendering medium. Match the new courtyard lighting and contact shadows to the foreground subjects. Do not change the child's expression or the dragon's eye shape.`

## 3. The official T2I eight-step method, condensed

This section is a *paraphrase* of the official Qwen prompt-rewriter instructions, not a verbatim reproduction.

**Step 1: Separate fixed requirements from creative freedom.** Preserve exact strings, named objects, counts, colors, positions, and user-specified ratio. Freely decide only unspecified details.

**Step 2: Fix orientation and aspect ratio.** The official rewriter favors 3:2 for general horizontal scenes and 2:3 for vertical scenes unless another ratio is given. It also recognizes 1:1, 16:9, 9:16, and specialized banners. The rewriter's structured output places ratio in a separate `wh_ratio` field rather than restating dimensions in prose.

**Step 3: Introduce the complete scene in one sentence.** Identify medium, style, primary subject, background/palette, and orientation when appropriate.

**Step 4: Prepare an inventory of visual elements.** Assign each object a place in the frame; separately inventory *all exact visible text*, in reading order. The official process targets a richly localized frame with roughly 8–14 position descriptions, as complexity permits.

**Step 5: Describe the frame in spatial order.** For posters and panels, proceed from background to top, then left/center/right, then bottom. For a single subject, describe the environment and then its pose, face, clothing, attachments, and remaining boundaries.

**Step 6: Treat lettering as geometry and content.** Quote literal strings; identify location, type weight, color, relative size, language/script, and intentional line breaks. Do not invent readable signs the user never requested.

**Step 7: State the light explicitly.** Describe source, direction, diffusion/hardness, and plausible shadows/reflections.

**Step 8: Close with overall cohesion.** One ending sentence about balance, palette, medium, and emotional character.

**Official writing conventions:** Rich descriptions, often around 20 sentences and 400–500 words for complex compositions, rather than short keyword stacks; declarative visual prose instead of command verbs; concrete colors such as muted lavender or deep charcoal; real surface materials; explicit small item counts; no empty quality boosters like `masterpiece`, `8K`, or `award-winning`. The 400–500-word target describes the official *rewriter's preferred expansion style*, not a demonstrated hard optimum for every possible request. Simple single-subject images can and should be shorter.

## 4. Master text-to-image framework

Use this reusable outline, then write it as natural contiguous prose, not as a prompt filled with field labels:

```
[A wide/vertical/square] [medium] in [art style], depicting [primary subject]
[in action], against [specific setting and palette]. [Background materials,
atmosphere, and horizon]. [Top-of-frame elements]. [Left-side elements].
[Central subject position, proportions, appearance, pose, face, expression,
clothing and interacting objects]. [Right-side elements]. [Near foreground and
bottom]. [Exact textual content and styling, when any]. [Visible materials,
textures, edge treatments and rendering techniques]. [Light source, direction,
quality and resulting shadows]. The overall composition [balance, color harmony,
readability, depth and mood].
```

**Content priority, a heuristic:** (1) subject and action; (2) preservation or exact hard constraints; (3) spatial relationships; (4) medium/style; (5) light/material; (6) small decorative details. Longer does not necessarily mean better if details conflict.

**When to describe explicitly:** face direction, hand usage, number of limbs, object ownership, exact text, camera crop, left/right arrangement, tail shape, silhouettes, focal subject scale, and an area reserved for later text. **When to avoid invention:** names, logos, posters, captions, extra people, jewelry, accessories, signage, or visual symbols outside the request.

## 5. Master editing framework

```
[Operation verb] [precise target/location] [specific result]. Use <image1> as
the base canvas and, if present, use <image2> only for [specific source
attribute]. Preserve [subject identity/face/body proportions/pose/costume] and
[all untouched composition/background/medium/lighting/text]. Integrate the
changed area with [perspective, occlusion, light direction, shadows,
reflections, material response]. Change nothing outside [target scope].
```

For **one input image**, Qwen's official rewriting instructions recommend referring to `the image` rather than using a numbered tag. For **two or more**, the official prompt-enhancer uses `<image1>`, `<image2>`, and so on (without spaces) and assigns a clear role to each. Some community interfaces use `<image 1>` or another UI convention; use the syntax expected by your workflow. Image count support depends on deployment.

### Editing operations dictionary

- **Replace:** `Replace the [specific object] with [new object], preserving its size/position/occlusion and scene lighting.`
- **Recolor:** `Change only the [named surface] to [target color], preserving texture, reflection, and shading.`
- **Remove:** `Remove [object] and reconstruct the surface behind it consistently with neighboring texture and perspective.`
- **Add:** `Place [object] at [position], with correct scale, contact shadow, occlusion, and material response.`
- **Move:** `Move [object] to [location], restore the space it formerly occupied, and keep every other subject fixed.`
- **Relight:** `Change scene illumination to [source/direction/color], preserving geometry, color identity where required, and scene layout.`
- **Restyle:** `Render the image in [medium and technique], preserving [identity, pose, silhouette, spatial arrangement].`
- **Outpaint:** `Outpaint the canvas [direction] to reveal [scene continuation]. Keep existing content and scale unchanged.`
- **Text:** `Replace only the text "OLD" with "NEW". Preserve layout, perspective, weight, color, and substrate.`
- **Pose transfer:** `Keep the identity/clothing of <image1>; use <image2> solely as the pose reference.`
- **Environment swap:** `Use the foreground subjects from <image1> and the environment/light logic of <image2>. Keep foreground identities untouched.`

### Preserve versus change

Think in two columns before authoring: **mutable properties** and **invariants**. For style transfer, color/linework/material rendering change but subject identity and composition remain. For camera angle shifts, viewpoint changes but identity and recognizable object geometry should remain. For costume swaps, attire changes but face, build, and pose remain. Describe those boundaries explicitly.

**Official important nuance:** Identity is especially fragile. A source photo should anchor a person's identity; do not replace visual identity with a fresh textual face description if fidelity to the original is the goal. Avoid incidental 'cleanup' of anything not requested.

## 6. Multi-reference prompt design

**Official model capabilities:** up to ten reference images, depending on implementation. Qwen describes localized edit selection via drawn circles, painted annotations, and separate masks. More references are *possible*, but not automatically better. Assign each reference a single role whenever feasible.

### Reference role map

This table illustrates a **base-canvas edit**, not a mandatory assignment for all
tasks. For a new scene built from two character references, both inputs instead
supply identities as described in the automation supplement above.

| Input | Role | Transfer | Preserve |
|---|---|---|---|
| `<image1>` | Base scene | Canvas/composition | All non-target areas |
| `<image2>` | Character | Face, costume, body design | Identity across output |
| `<image3>` | Pose | Skeletal arrangement | Do not import person's face |
| `<image4>` | Palette/style | Color treatment, edge technique | Do not import unrelated objects |
| `<image5>` | Environment | Architecture, setting/light cues | Keep main character from image 2 |

**Example:**

```
Use <image1> as the base image and preserve the placement of all existing
objects. Replace only the central character with the person in <image2>,
retaining that person's facial identity, hairstyle, and outfit. Match the
central character's pose to <image3>, without copying the identity or clothes
from <image3>. Apply the warm, textured marker-and-ink rendering technique
visible in <image4> to the entire finished illustration. Match shadows and
perspective to the setting in <image1>.
```

**Heuristic:** Introduce additional references gradually and inspect whether identity, clothing, perspective, or style leaks across references. If a local edit keeps affecting other parts, apply a supported mask or obvious local marker and state exactly which marked region is allowed to change.

## 7. Composition, aesthetics, and design vocabulary

### Framing

**Shot scale:** extreme close-up, close-up, bust, waist-up, three-quarter body, full-body, environmental wide shot, establishing shot.  
**Camera position:** eye-level, overhead, low-angle, profile, three-quarter view, frontal, bird's-eye, shoulder-height, ground-level.  
**Subject placement:** dead-center symmetry, right third, off-center left, diagonal motion, triangular arrangement, leading lines, foreground silhouette.  
**Focus:** narrow depth of field, soft bokeh, deep focus, crisp silhouette against distant atmospheric haze.  
**Negative space:** `the upper 30 percent is visually quiet for typography` is much more actionable than `leave room for text`.

### Light

Describe at least three of: **source** (`window`, `overcast sky`, `late sun`, `studio softbox`), **direction** (`upper-left`, `back-right`), **quality** (`diffuse`, `hard`, `rim-lit`), **temperature** (`warm amber`, `cool blue-grey`), **effects** (`soft contact shadows`, `specular highlights`). Lighting must be compatible with the scene's geometry.

### Texture and medium

- **Photographic:** natural skin texture, subtle pores, realistic catchlights, believable lens depth, material imperfections, gentle tonal transitions.
- **Ink and marker:** assertive dark contour lines, visible pen strokes, controlled ink fill, alcohol-marker bleed, layered translucent marker strokes, limited palette, paper fibers, handmade irregularities.
- **Watercolor:** translucent washes, pigment granulation, bleeding edges, reserved paper highlights, uneven handmade saturation.
- **Editorial/vector:** strong shape hierarchy, flat fills, controlled outline weights, sharp curves, disciplined whitespace.
- **3D illustration:** rounded expressive forms, subsurface-like skin response, carefully designed cloth, sculpted silhouette, cinematic softbox lighting.
- **Pencil/charcoal:** cross-hatching, stippling, powdery value gradients, graphite tooth, subtle smudging.
- **Product photography:** matte versus glossy surfaces, accurate labels and proportions, realistic reflections, grounded contact shadow.

**Heuristic:** Avoid contradictory media instructions (`pure vector with heavy photographic film grain`), unless the *mixture* is intentional and specified as a hybrid.

## 8. Text and typography: treating words as visual objects

Qwen's official guide places particular emphasis on **exact literal strings**, *including punctuation, non-English scripts, and chart/table labels.* You should specify where each string appears, how large it is relative to others, its styling and alignment, and how line breaks are represented.

### General rules

1. Put visible strings in double quotation marks.
2. State that only the specified words are readable when this is a requirement.
3. Identify **each text block separately** with location, hierarchy, font style, color and approximate relative scale.
4. Preserve accents, capitalization, punctuation, diacritics, and symbols exactly.
5. For multilingual signage, preserve the original script for rendered text even when the rest of the description is English.
6. Describe separate lines clearly rather than depending on embedded newline characters.
7. If dense, mechanically exact text is critical, consider adding it in a design editor after image generation; model rendering is not guaranteed error-free.

**Example:**

```
The upper third holds a two-line Spanish headline. The first line reads
"UN AÑO, UNA LEYENDA" in large silver condensed display capitals. Directly
below, the second line reads "EL DEBUT DE ABI" in smaller deep-purple
hand-lettered capitals. The letters are evenly spaced, fully legible, and
unobstructed. No other readable words appear in the image.
```

### Infographics and tables

Define axes, legends, callouts, title, footer, icon placement, cell labels, and values individually. If the image functions as an educational graphic, provide the facts and text in a structured source before composing the prose. **Do not trust a visually plausible image as factual validation**; verify numbers and copy separately.

## 9. Transparent image creation and extraction

**Official RGBA prompt wrapper:**

```
This is an RGBA image with transparency. A cute cartoon dragon sticker.
The image has alpha channel and the background is transparent.
```

Replace the middle description with the intended asset. The model supports native RGBA generation, but ensure your runtime preserves the alpha channel and saves to PNG or another alpha-capable format. JPEG destroys transparency.

**Transparent sticker example:**

```
This is an RGBA image with transparency. A full-body, friendly charcoal-black
dragon mascot in ink-and-marker illustration style, three-quarter view,
standing upright with a long tapering tail curling to the left. Clean
complete silhouette, expressive emerald eyes, no cast shadow outside the
subject, and no scenery or lettering. The image has alpha channel and the
background is transparent.
```

**Subject extraction edit:** `Extract only the main dragon from the provided illustration as a transparent RGBA cutout. Preserve its complete silhouette, line quality, colors, facial expression, tail tip and wings. Remove the background without changing the subject.`

**Heuristic post-checks:** inspect alpha with an image tool; check edge halos; ensure neither background color nor transparency checkerboard has become part of the pixels; inspect antialiasing at real export scale.

## 10. Specialized prompt recipes

### A. Children's birthday invitation background (T2I)

```
A wide hand-drawn ink-and-alcohol-marker illustration of a friendly
charcoal-black dragon gliding high above a storybook valley, carrying a
happy toddler safely seated between its shoulders. The dragon spans the
central-right region of the frame, with broad open wings and a thick tail
that tapers continuously toward a fine rounded tip. The toddler has dark
wavy hair moving gently backward in the wind and an expressive joyful face.
Layered clouds drift through the lower foreground, while a whimsical stone
castle rises on a distant hill near the bottom left. The upper-left third
remains calm and uncluttered, with pale sky gradients suitable for later
invitation text. Dark expressive pen contours, translucent cool-blue marker
layers, soft paper texture, and warm muted lavender accents establish a
storybook illustration. Diffuse late-afternoon sunlight from the upper
left illuminates the characters and creates soft shadows beneath the
wings. The overall composition feels adventurous, gentle, and festive.
```

**Choose 16:9 or 3:2 externally**. If text will be added afterward, keeping it *out* of the image generation request often increases control.

### B. Change only the dragon's tail (I2I)

```
Reshape only the dragon's tail so its diameter gradually and smoothly
decreases from a broad base at the hips to a narrow rounded tip, with no
sudden changes in thickness, joints, or pinched sections. Preserve the
dragon's original body proportions, wing shapes, head, eye design, pose,
scale pattern, colors, and illustration style. Leave the rider, background,
lighting and composition unchanged.
```

### C. Photo to ink-and-marker style (I2I)

```
Convert the supplied photo into a hand-drawn ink-and-alcohol-marker
illustration. Keep the subject's identity, facial expression, body
proportions, hairstyle, clothing, pose, and original framing unchanged.
Replace photographic rendering with confident dark contour strokes,
visible fine-marker linework, layered translucent alcohol-marker washes,
limited intentional color fills, subtle stroke overlaps, and off-white
paper grain. Keep the original light direction and spatial arrangement,
translated into marker shading. Do not introduce new objects or text.
```

### D. Exact sign or label replacement (I2I)

```
Replace only the words "OPEN TODAY" on the storefront sign with
"GRAN APERTURA". Keep the sign's original position, scale, frame,
lettering perspective, material, surrounding objects, and illumination.
The new lettering must be visibly integrated into the sign surface. Do
not modify any other text, faces, storefront details or background.
```

### E. Preserve one character while changing viewpoint (I2I)

```
Re-render the dragon from the input image in a three-quarter right view
from a slightly low camera angle. Keep its exact recognizable facial
design, eye color, ear silhouette, wing structure, body size, tail profile,
and drawing medium. Maintain the outfit and identity of the rider. Rebuild
only those body surfaces that naturally become visible due to the changed
viewpoint; do not add accessories or extra limbs.
```

### F. Product or object composite (multi-image I2I)

```
Use <image1> as the base tabletop photograph. Replace only the cup on
the table with the mug from <image2>, preserving the mug's exact handle,
shape, finish, logo, and proportions. Position it at the existing cup's
footprint with the correct perspective and contact shadow. Preserve all
other objects, the tabletop, the background blur, and the light coming
from the upper right.
```

### G. Character sheet (T2I or multi-reference edit)

```
A clean character-design turnaround sheet featuring the same friendly
black dragon in four evenly spaced full-body views: front, left profile,
rear, and three-quarter front. Each drawing has matching anatomy, hornless
head, pointed upright ears, wing membrane shape, emerald eyes, scale
color, and smoothly tapered tail. The views share the same relative size
and baseline on a plain warm off-white background. Ink-and-marker outlines,
subtle uniform marker shading, no props, no scenery, no readable labels.
```

**Heuristic:** When consistency is critical, generate a single approved character first and use it as a reference for subsequent views rather than hoping every panel will independently reproduce the same design.

### H. Cinematic portrait (T2I)

```
A vertical editorial color photograph of a young adult violinist standing
beside a rain-streaked window in a muted amber-and-blue rehearsal room.
The violinist fills the right half of the frame, looking toward the soft
window light at left, holding an antique wooden violin below the chin.
Natural skin texture and loose hair strands remain visible, while the
background shelves dissolve into gentle shallow-depth-of-field bokeh.
Cool window illumination falls across the cheek and hands, balanced by a
subtle warm lamp deeper in the room. The composition is intimate,
understated, and naturally photographic.
```

### I. Logo/icon (T2I)

```
A centered square minimalist vector-style emblem of a dragon curled
into a circular silhouette. A single continuous bold charcoal contour
suggests its head, folded wings and tapering tail. One small emerald-green
eye provides the only accent color. Balanced negative space, crisp clean
shapes, no gradients, no letters, and an off-white field. The overall
mark is recognizable at small icon size.
```

### J. Outpainting (I2I)

```
Outpaint the image horizontally to create a wide panoramic scene.
Preserve the entire existing central image, including exact character
identities, scale, position, rendering medium, and the current horizon.
Extend the left boundary into rolling hills and distant mist; extend the
right boundary into an open cloudy sky above the same valley. Continue
lighting, perspective, color temperature, and cloud texture seamlessly.
Do not stretch or repaint the original subjects.
```

### K. Background-only swap (I2I)

```
Replace only the background with a distant fairy-tale castle surrounded
by pale clouds. Keep the foreground child and dragon pixel-faithful in
identity, proportions, expression, pose, outfit, silhouette and color.
Preserve the original ink-and-marker medium and soft atmospheric lighting.
Match depth and edge integration without moving either foreground subject.
```

### L. Infographic (T2I)

```
A vertical modern educational infographic explaining the water cycle,
on a pale cream background with navy headings and muted blue diagrams.
At the top, a large centered heading reads "THE WATER CYCLE". Below it,
a circular diagram contains a bright water surface at lower left, clouds
across the top, and a vegetated hillside at lower right. Four arrows link
four clearly separated labels reading "EVAPORATION", "CONDENSATION",
"PRECIPITATION", and "COLLECTION" in that order around the cycle.
Each label has adequate spacing and a clean dark sans-serif appearance.
Soft flat shading, coherent arrow directions, and evenly distributed
negative space make the scientific process easy to follow.
```

## 11. Parameters, runtime, and prompt-enhancement workflow

### Official local runtime examples

```python
import torch
from diffusers import QwenImage21Pipeline

pipe = QwenImage21Pipeline.from_pretrained("Qwen/Qwen-Image-2.1", torch_dtype=torch.bfloat16).to(
    "cuda"
)

image = pipe(
    prompt="A wide ink-and-marker illustration of a black dragon above clouds.",
    width=2752,
    height=1536,
    num_inference_steps=40,
    generator=torch.Generator("cuda").manual_seed(42),
).images[0]
image.save("dragon.png")
```

The official repository recommends the base checkpoint's default **40 steps** and documents sizes such as `2048x2048`, `2400x1792`, `1792x2400`, `2528x1696`, `1696x2528`, `2752x1536`, and `1536x2752` for common ratios. Those are recommended open-checkpoint configurations, not necessarily allowed sizes for every hosted service.

**Turbo:** use `Qwen/Qwen-Image-2.1-Turbo` and let the model load its saved eight-step sampling schedule. The official README explicitly notes that changing `num_inference_steps` alone does not replace that schedule; its illustrated default CFG is 1. Respect the bundled settings rather than copying older Qwen-image sampler values.

**Memory:** The official model card demonstrates `enable_model_cpu_offload()` as a memory optimization. Tiled operations, quantization, and ComfyUI setups vary by hardware. Record precision, offload mode, prompt rewriting, sampler configuration, dimensions, and seed with every comparison.

### Official prompt rewriting models

- `Qwen/Qwen-Image-2.1-PE-T2I`: task-specific text-to-image expansion.
- `Qwen/Qwen-Image-2.1-PE-I2I`: task-specific edit expansion, incorporating reference content.
- Official repo: `prompt_rewrite/`, with task selection `--task t2i` or `--task edit`, Transformers and vLLM examples.

A useful production system has a short **user brief**, a separate **prompt enhancer**, a **renderer**, an **image evaluator**, and a targeted **revision** phase. Preserve a trace of brief -> expanded prompt -> images -> feedback. Rewrite once, inspect, then revise only the failing requirement rather than indiscriminately making every prompt longer.

### About negative prompts

Do not make `negative_prompt` a required part of your Qwen-Image-2.1 prompt schema. The current Alibaba hosted 2.1 series does not support that API parameter. Where you want to exclude features, use explicit constraints: `a hornless dragon with pointed ears`, `an unobstructed sky above`, `no readable lettering`. Whether a specific *local* UI supports a separate negative-conditioning workflow should be checked against its implementation.

## 12. Troubleshooting matrix

| Problem | Likely issue | Targeted fix |
|---|---|---|
| Subject identity drifts after editing | Model reimagines subject instead of using source | Refer directly to source image and state facial/body invariants; avoid fresh face descriptions |
| Extra objects or decorations appear | Underconstrained inventory | State exact counts; list disallowed extras in plain descriptive constraints |
| Text is misspelled | Text content and layout are underspecified | Quote exact strings, assign region/font hierarchy; proofread or overlay text in an editor |
| Edits affect untouched objects | Editing scope too broad | State operation first; add target location and preservation clause; use mask/annotation if available |
| Confusing multi-image composite | Images have ambiguous roles | Assign each reference a role and explicitly prohibit transferring other attributes |
| Tail/limbs/anatomy inconsistent | Weak geometric specification | Describe thickness transitions, joints, camera angle, occlusion, and visible limb counts |
| Style transfer destroys likeness | Changed style entangled with changed identity | Describe linework and shading as mutable, face/silhouette/costume as fixed |
| Scene looks flat | Lighting and foreground missing | Name directional light, shadows, occlusion, material and depth layers |
| Typography overlaps subjects | Space not reserved geometrically | Specify percentage/region of negative space; constrain where subjects occupy frame |
| Transparent PNG contains white background | Prompt/runtime not using true RGBA | Use official RGBA wrapper; inspect alpha in PNG and avoid JPEG |
| New background looks pasted in | Perspective and shadow mismatch | Specify light direction, focal depth, contact shadows, horizon and occlusion |
| Huge prompt worsens image | Contradictory or low-priority detail | Remove conflicting qualifiers; keep hard constraints and important geometry |
| Turbo quality inconsistent | Incorrect sampler assumptions | Load Turbo's saved schedule rather than forcing base-model parameters |
| Local pipeline crashes on large output | Insufficient device memory | Use offload, smaller dimensions, supported optimizations or hosted endpoint |

## 13. Reusable prompt-enhancer instructions

This is an *original, simplified meta-prompt*, inspired by the published Qwen guidance, not a copy of the official system prompt.

### A. Text-to-image prompt enhancer

```
You specialize in writing descriptions for Qwen-Image-2.1 text-to-image.
Expand the user's brief into a coherent, detailed depiction of the finished
image in English. Preserve every explicit subject, count, color, position,
visible text string, and requested aspect ratio. Do not add text unless the
user asks for it. Start with orientation, visual medium, style, main subject,
and setting. Describe background first, then walk the frame spatially from
top to bottom and left to right, or describe a central subject from pose to
facial details and materials. Place all requested lettering in quotes and
specify its size, location, and treatment. Describe surface materials and
lighting direction, quality, and shadows. End with one sentence about
composition and palette. Write in present tense as an observer, not as an
imperative to the model. Avoid empty superlatives and contradictory style
adjectives. Provide the recommended aspect ratio separately from the prompt.
Do not silently change the user's specified creative decisions.
```

### B. Image editing prompt enhancer

```
You specialize in rewriting edit instructions for Qwen-Image-2.1.
Inspect the supplied images first if they are available to the refiner; otherwise
use their declared roles without inventing visible details. Produce an actionable editing directive
that begins with the requested operation and names the precise visual
target. For multiple images use <image1>, <image2>, etc., and specify the
role of each reference. Preserve everything not explicitly targeted,
particularly identity, facial features, body shape, outfit, pose,
composition, existing text and rendering medium. If a new scene is
requested, design its environmental details and coherent lighting while
preserving the source subject's identity; permit the requested pose and composition
changes rather than locking them to the references. Quote all exact visible text. If content is
removed, explain how the revealed region should be reconstructed. Name
outpainting explicitly if the canvas is extended. Do not invent extra
edits, accessories, cleanup, or text. Use the requested language for
literal text within the image.
```

### C. Iterative critique prompt

```
Compare the generated image against the original brief and reference
images. Score identity fidelity, hard-constraint satisfaction, composition,
style consistency, typography accuracy, geometry, lighting and visual
artifacts from 0 to 5. Identify at most three substantive failures with
precise image-region descriptions. Recommend the smallest edit needed to
address each failure. Do not rewrite the entire prompt unless multiple
foundational constraints failed. Never claim to have verified visible
text without checking it character by character.
```

## 14. A repeatable evaluation protocol

**Recommended experiment protocol (heuristic):** Test 3–5 seeds for a meaningful prompt change; hold model, resolution, seed, sampler, and references constant when comparing *prompt structure*. For one-attribute edits, inspect whether unrelated regions shifted. For complex output, check at final print or screen scale. Keep the short user brief alongside the expanded version so you can catch prompt-enhancer hallucinations.

### Rubric

| Dimension | What to evaluate | Suggested weight |
|---|---|---:|
| Hard constraints | Exact counts, positioning, required elements | 25% |
| Identity & consistency | Reference likeness and visual preservation | 20% |
| Spatial composition | Framing, hierarchy, focus, reserved whitespace | 15% |
| Rendering & materials | Correct medium, texture and craft | 15% |
| Light & physical coherence | Shadows, reflections, scale, occlusion | 10% |
| Text fidelity | Correct spelling, positioning and hierarchy | 10% |
| Artifacts | Extra limbs, merged edges, halos, unintended items | 5% |

Adjust weights to use case: logos and invitations often need greater typography priority; people/character work needs greater identity weight; product shots need material and markings precision.

### Output metadata to preserve

Save `model_id`, revision/checkpoint, prompt, original user brief, expansion model/version, reference-image names/order, seed, dimensions, steps, guidance/sampling schedule where applicable, UI or inference backend version, creation timestamp, and evaluator notes. This makes prompt comparisons reproducible rather than anecdotal.

## 15. Source library

### Tier 1: Qwen primary sources

1. [Qwen-Image-2.1 official GitHub repository](https://github.com/QwenLM/Qwen-Image-2.1) — releases, capability definitions, pipeline examples, transparency, recommended resolutions, Turbo schedule, and official rewriting entrypoints.
2. [Official T2I prompt-rewriter system instructions](https://github.com/QwenLM/Qwen-Image-2.1/blob/main/prompt_rewrite/prompts/system_prompt_t2i.txt) — highest-value source for spatial narrative, exact text, material/color naming, writing style and aspect-ratio logic.
3. [Official I2I/edit prompt-rewriter system instructions](https://github.com/QwenLM/Qwen-Image-2.1/blob/main/prompt_rewrite/prompts/system_prompt_edit.txt) — highest-value source for preservation, reference roles, localized edits, and edit instruction structure.
4. [Qwen-Image-2.1 model card](https://huggingface.co/Qwen/Qwen-Image-2.1) — official model details, Diffusers usage, RGBA syntax, aspect ratios and memory optimization.
5. [Qwen-Image-2.1 Turbo model card](https://huggingface.co/Qwen/Qwen-Image-2.1-Turbo) — accelerated checkpoint and recommended runtime behavior.
6. [Qwen's Image 2.1 launch article](https://qwen.ai/blog?id=qwen-image-2.1) — official capabilities, architecture, and intended editing workflows.
7. [Qwen-Image-2.1 T2I prompt-enhancer checkpoint](https://huggingface.co/Qwen/Qwen-Image-2.1-PE-T2I) — model-based prompt enhancement.
8. [Qwen-Image-2.1 I2I prompt-enhancer checkpoint](https://huggingface.co/Qwen/Qwen-Image-2.1-PE-I2I) — reference-aware edit prompt enhancement.
9. [Alibaba Cloud Qwen Image Edit guide](https://www.alibabacloud.com/help/en/model-studio/qwen-image-edit-guide) — hosted parameters, negative prompt support, seed and prompt expansion.
10. [Alibaba Cloud Image Generation and Editing API](https://www.alibabacloud.com/help/en/model-studio/qwen-image-generation-and-editing-api-reference) — hosted API shapes; verify model-specific limits.
11. [Original Qwen-Image project](https://github.com/QwenLM/Qwen-Image) — historical context and prior model-specific examples. **Do not transplant parameters blindly to 2.1.**

### Tier 2: Community guides and feedback

12. [Reddit: Qwen 2.1 editing tips](https://www.reddit.com/r/StableDiffusion/comments/1wlt9t9/qwen_image_21_edit_tips/) — community examples of pose/texture/light/background transfers.
13. [Reddit: Qwen Image 2.1 is an editing beast](https://www.reddit.com/r/StableDiffusion/comments/1wokni7/qwen_image_21_is_an_editing_beast/) — user experiments on products, poses, and preservation.
14. [Reddit: Qwen 2.1 first impressions](https://www.reddit.com/r/StableDiffusion/comments/1wlgv0l/i_tested_qwenimage21_its_amazing/) — community claims about references, text, RGBA, and contrast limitations.
15. [Reddit: simple Qwen 2.1 workflows](https://www.reddit.com/r/StableDiffusion/comments/1wm77xo/some_very_simple_qwenimage21_workflows/) — ComfyUI editing, outpainting, transparency, masking, reference workflows.
16. [ComfyUI workflows by Nomadoor](https://comfyui.nomadoor.net/en/basic-workflows/qwen-image-2-1/) — practical reproducible workflow starting points, community-authored.
17. [Reddit: 20+ ComfyUI Qwen 2.1 workflows](https://www.reddit.com/r/sdforall/comments/1wu7cww/comfyui_qwen_image_21_tutorial_generate_edit/) — tutorial inventory including masks, LoRAs, background removal and multi-image cases.
18. [Reddit: prior Qwen Image Edit prompt playbook](https://www.reddit.com/r/StableDiffusion/comments/1n1n81o/qwenimageedit_prompt_guide_the_complete_playbook/) — useful historical prompt patterns; mostly older model generation.
19. [Community Qwen 2.1 prompt guide examples](https://github.com/kjranyone/qwen-image-2.1-prompt-guide/blob/main/skills/qwen-image-prompt-en/references/examples.md) — additional prompt examples and cross references. Check original evidence before adopting each claim.

---

## Executive principles: the shortest reference card

**For T2I:** (1) Name the medium. (2) Describe the image as already visible. (3) Place elements explicitly. (4) Quote exact text. (5) Specify materials and light. (6) End with overall cohesion. (7) Use a separate aspect ratio setting. (8) Avoid generic quality incantations.

**For I2I:** (1) Start with the action. (2) Name the exact target. (3) Assign reference roles. (4) State non-negotiable invariants. (5) Integrate shadows/materials/perspective. (6) Change nothing extra. (7) Use masks for local edits where supported. (8) Recheck source identity and original text.

**For reliable experimentation:** use the official PE-T2I and PE-I2I models as baselines; compare with hand-authored prompts; log seeds and checkpoint details; evaluate constraint compliance before artistic taste; prefer small controlled revisions over total regeneration of the brief.

*This is a living guide. Sources are linked rather than reproduced wholesale, especially the official rewriting prompts. Treat unsupported community claims as hypotheses to test on your own pipeline.*
