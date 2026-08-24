<!-- diataxis: how-to -->

# Choose the source derivative cap

`plone.pgthumbor` ships `PGTHUMBOR_SOURCE_MAX_EDGE` with a default of `4000` pixels.
That default is a safe starting point for a site nobody has measured.
It is not a recommendation for your site.

The cap decides how much of the original Thumbor ever gets to see.
Set it too high and every derivative costs storage and resize memory it does not need.
Set it too low and heavily cropped renditions soften, because the crop is taken from a smaller source.
The value that fits sits between two numbers only your own content can tell you.

This guide measures those numbers, turns them into a value, and applies it.
Changing your mind later costs one more backfill run, so the first answer does not have to be the final one.

## Before you start

- `plone.pgthumbor` installed, with the GenericSetup profile upgraded to version 5.
- The backfill script in the container, and a Plone instance you can run `zconsole` in.
  {doc}`backfill-source-derivatives` covers both.
- Read access to the production database, or to a recent copy of it.
  The dry run writes nothing, so a copy is enough.

## Step 1: measure

Run the backfill in dry run mode:

```bash
docker exec -it <container> \
    env PGTHUMBOR_BACKFILL_DRY_RUN=1 \
    zconsole run etc/zope.conf /tmp/backfill.py
```

It reports four numbers and writes nothing:

```text
DRY RUN at cap 4000px — nothing was written.
  candidates:        1263
  median derivative: 2118400 bytes (from 25 of the first 25 by zoid)
  uids that will move: 84 (candidates with no _modified; every scale uid for
  those images changes when the derivative is written)
  scales carrying crops:
    preview: 143
    albumfull: 12
```

| Number | What it tells you |
|---|---|
| `candidates` | How many image field values still need a derivative at this cap. |
| `median derivative` | The byte cost of one derivative, sampled by actually encoding a few. Multiply by the candidate count for the storage the run adds. |
| `uids that will move` | Field values with no `_modified` attribute. Writing a derivative moves their `_p_mtime`, and every scale uid hashed against it moves too, so catalog metadata and cached HTML for those images go stale in one step. This is the cache invalidation blast radius, not an error. |
| `scales carrying crops` | The scale names that actually have crop boxes stored, with a count each. This is the input to step 2. |

The crop histogram takes a sequential scan and the sample decodes real images, so the run can sit quiet for minutes before it prints.

## Step 2: find the binding scale

Take the scale names from the histogram and look up their widths.
Registered scales live in the registry record `plone.allowed_sizes`, one `name width:height` entry per line, editable under Site Setup, Image Handling.

The binding number, call it *S*, is the width of the **largest scale that actually carries a crop**.
It is not your largest registered scale.
Crops are stored per scale name, so a site that registers `albumfull 1600:1600` but only ever crops `preview 400:0` has an *S* of 400, not 1600.

If the histogram is empty, no editor has cropped anything, and the crop threshold does not constrain your cap at all.
Skip to step 4.

## Step 3: apply the threshold

A crop covering fraction *X* of the derivative's edge feeds `cap * X` source pixels into a rendition of width *S*.
The result stays lossless while:

```text
X >= S / cap
```

Below that the rendition softens.
It does not break, and nothing errors: the crop is simply enlarged from fewer pixels than it wants.

| Largest cropped scale (*S*) | Threshold at cap 4000 | Threshold at cap 5000 |
|---|---|---|
| 1600 | 40 % | 32 % |
| 460 | 11.5 % | 9.2 % |
| 400 | 10 % | 8 % |
| 175 | 4.4 % | 3.5 % |

Read the row for your *S* and ask whether your editors crop tighter than that.
A site whose largest cropped scale is 400 has a threshold of 10 % at the default cap, which almost no editorial crop reaches.
A site that crops a 1600 pixel scale has a threshold of 40 %, which editors reach routinely, and that is the case for raising the cap.

A second, harder floor comes from high pixel density scales.
High pixel density candidates request `scale width * density`, and `plone.pgthumbor` clamps every requested dimension to the selected source.
With `plone.highpixeldensity_scales` set to `2x`, a 1600 pixel scale asks for 3200 pixels, and a cap below that serves the candidate at the cap's width while the `srcset` descriptor still claims the higher density.
Keep the cap at or above the largest registered scale width multiplied by the highest density factor in use.

## Step 4: set the value

Set the environment variable on the Plone process:

```bash
PGTHUMBOR_SOURCE_MAX_EDGE=5000
```

Or edit the `source_max_edge` field in the Thumbor control panel, which stores it in the registry.
The environment variable wins where both exist.
{doc}`../reference/configuration` documents both, along with the `0` kill switch and the `8000` ceiling.

## Step 5: measure again, then run

Run the dry run a second time.
The candidate count is now measured against the new cap, and it includes every image whose recorded cap no longer matches, so it tells you exactly how much work the change creates.

When the number looks right, run the backfill for real.
{doc}`backfill-source-derivatives` has the procedure, including the second phase that repairs the catalog.

## Changing the cap later

Each derivative records the cap it was generated under.
A recorded cap that no longer matches the configured one makes that image a candidate again on an **ordinary** backfill run.
No `force` flag, no recheck mode, nothing to remember.

That is deliberate.
On a generic package every deployment tunes this number at least once, and a tuning step that silently does nothing unless you pass the right flag is a trap.
The practical consequence: the cap is a setting, not a migration, and the decision stays open.

One cost is worth knowing before the second run.
Replacing a derivative orphans the old one.
Nothing references the displaced blob from that moment on, and nothing in this package collects it, so repeated re-tuning accumulates blobs that only your storage's own garbage collection can reclaim.

## What going up costs

Storage and resize memory scale with the square of the cap.
Raising it from 4000 to 5000 costs roughly 1.56 times as much of both per derivative.
The `median derivative` figure from step 1 is the honest input here: on detailed press imagery, JPEG at quality 92 with no chroma reduction can land well above the 2 MB a rough estimate suggests.

The ceiling of `8000` is arithmetic, not taste.
A longest edge of *E* bounds the derivative at *E²* pixels, and Thumbor refuses anything above its `MAX_PIXELS` limit of 75 megapixels.
Above `sqrt(75e6)`, roughly 8660, a derivative could reproduce the very HTTP 400 the feature exists to remove, and it would do so silently: generation succeeds, and only Thumbor objects.
Values above `8000` are clamped when they are read, including values already stored in the registry before the bound existed, so the guarantee cannot be configured away.
