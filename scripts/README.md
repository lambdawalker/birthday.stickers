# Standalone image tools

These scripts are separate from the generation application. They do not read its
YAML, sessions, or metadata and do not call an LLM or ComfyUI.

## Center a black dragon

From the repository root (PowerShell or any shell):

```powershell
uv run scripts/center_dragon.py "C:\pictures\stiker-0001.jpg"
```

`uv` uses the script's inline dependency declaration to install Pillow in an
isolated environment. Alternatively, with Pillow installed:

```powershell
python scripts/center_dragon.py "C:\pictures\stiker-0001.jpg"
```

The original stays unchanged. By default, four files are saved beside it:

- `stiker-0001.centered.jpg`: square crop with 15% minimum padding on each side.
- `stiker-0001.centered.detection.png`: red detection box/center, cyan crop box.
- `stiker-0001.centered.mask.png`: selected dark regions in white.
- `stiker-0001.centered.json`: bounding box, center, crop coordinates, and notes.

Center means **the center of the detected bounding box**, including wings and
tail, rather than the face or visual center of mass. Pixel rounding can introduce
at most half a pixel of offset. The crop preserves the original resolution unless
`--size` is supplied. An edge-crossing crop is padded instead of shifted; padding
cannot restore a body part already cut off in the source image.

```powershell
uv run scripts/center_dragon.py "scene.jpg" --output "crops/dragon.jpg" --padding 0.25 --size 1024 1024
uv run scripts/center_dragon.py "scene.jpg" --aspect 4:3 --background "#f5e5ed"
```

### Detection and tuning

The detector groups connected dark pixels and chooses the group with the most
very dark seed pixels. It includes nearby overlapping dark components, such as
a wing separated by highlights, and deprioritizes dark frames touching three or
more canvas edges. It uses only colors and geometry, **not semantic recognition**.
It can mistake another dark object for a dragon or include touching shadows.

- `--threshold 145`: maximum R, G, or B value allowed in the dark region. Increase
  it to include brighter dragon highlights; decrease it if scenery gets included.
- `--seed-threshold 80`: maximum channel value for very dark seed pixels.
- `--close-radius 2`: bridge small gaps in the mask; zero disables this step.
- `--max-side 1200`: largest detection dimension; increase for fine details in
  large images. Bounding coordinates are mapped back to the original image.
- `--padding 0.15`: margin per side as a fraction of detected width/height.
- `--aspect W:H`: crop aspect ratio; square by default, or the ratio of `--size`.
- `--size WIDTH HEIGHT`: optional final resize, preserving the chosen proportions.
- `--force`: overwrite derived files. The input image cannot be overwritten.

Review the detection preview and mask before using the crop. This works best for
one black dragon against a light, colorful scene. Black clothing, dark night
scenes, detached body parts far from the main region, and large dark shadows may
need manual tuning or a future semantic segmentation model.

This tool creates separate files. It does not automatically replace generated
images or alter their downstream outpainting state.

### Process a directory

```powershell
uv run scripts/center_dragon.py "C:\pictures" --size 1024 1024
uv run scripts/center_dragon.py "C:\pictures" --recursive --output "C:\crops"
```

Directory mode supports JPG, JPEG, PNG, WebP, BMP, and TIFF (case-insensitive).
By default it processes only files directly inside the directory and saves results
under `INPUT/centered`. Use `--recursive` to include subdirectories; their structure
is preserved in the output. Batch names retain the original extension, for example
`scene.png.centered.jpg`, so `scene.jpg` and `scene.png` cannot collide.

The output directory and names containing `.centered`, `.detection`, or `.mask`
are excluded from discovery. Existing outputs are skipped; use `--force` to redo
them. If any of an image's four output files exists, the image is skipped, so use
`--force` to repair partial results. A failed image does not stop the batch; the
summary reports processed, skipped, and failed counts. Any failure gives exit code 1.
