<!-- diataxis: how-to -->

# Integrate plone.app.imagecropping

plone.pgthumbor ships with built-in support for
[plone.app.imagecropping](https://pypi.org/project/plone.app.imagecropping/).
When the addon is installed, manually defined crop regions are passed to
Thumbor so that the exact area chosen by the editor is used instead of
automatic (smart) cropping.

## Prerequisites

- A working plone.pgthumbor setup (see {doc}`install`).
- `plone.app.imagecropping` installed in the same Plone instance.

## How it works

No configuration is needed.
plone.pgthumbor detects `plone.app.imagecropping` at ZCML load time and
registers an `ICropProvider` adapter automatically.
When an image scale is
requested:

1. The adapter reads crop coordinates from the content object's annotations
   (where `plone.app.imagecropping` stores them).
2. If a crop box exists for the requested field and scale name, the
   coordinates are passed to Thumbor as an explicit crop region.
3. Thumbor crops the source image to those coordinates *before* resizing to
   the target dimensions.

When a crop is active, smart cropping is disabled for that scale and
`fit-in` mode is forced, because the editor has already defined the region
of interest.

## Verifying the integration

1. Install `plone.app.imagecropping` and activate it in your Plone site.
2. Navigate to an image content item and open the cropping editor
   (typically via the *Cropping* action or `@@croppingeditor`).
3. Define a crop for a specific scale (for example, *preview*).
4. View the page.
   Inspect the image URL -- it should contain crop coordinates
   in the format `{left}x{top}:{right}x{bottom}` before the dimensions.

Example URL with a crop region:

```
http://thumbor:8888/{signature}/10x20:300x400/fit-in/400x300/2a/ff
```

## Scales without a crop

If no crop has been defined for a particular field/scale combination, the
adapter returns `None` and Thumbor behaves as usual (applying smart
cropping or fit-in based on the scale mode and settings).

## Disabling the integration

If `plone.app.imagecropping` is not installed, no adapter is registered and
the crop lookup is skipped entirely.
This adds no performance cost.

To disable the integration while keeping `plone.app.imagecropping`
installed, remove the adapter registration by adding an override in your
project's `overrides.zcml`:

```xml
<adapter
    factory="plone.pgthumbor.addons_compat.imagecropping.ImageCroppingCropProvider"
    provides="plone.pgthumbor.interfaces.ICropProvider"
    for="*"
    remove="true"
    />
```
