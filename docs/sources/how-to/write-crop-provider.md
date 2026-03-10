<!-- diataxis: how-to -->

# Write a custom crop provider

plone.pgthumbor uses a pluggable `ICropProvider` adapter to look up crop
coordinates before generating Thumbor URLs.
You can register your own
adapter to provide crop coordinates from any source -- a custom annotation,
an external service, or computed values.

## Define the adapter

Create a class that implements `ICropProvider`.
The adapter receives the
content object as context.

```python
from plone.pgthumbor.interfaces import ICropProvider
from zope.interface import implementer


@implementer(ICropProvider)
class MyCropProvider:

    def __init__(self, context):
        self.context = context

    def get_crop(self, fieldname, scale_name):
        """Return (left, top, right, bottom) or None."""
        # Your logic here.
        # Return pixel coordinates as a 4-tuple of integers,
        # or None if no crop should be applied.
        return None
```

### Parameters

| Parameter | Type | Description |
|---|---|---|
| `fieldname` | `str` | Name of the image field (for example, `"image"`, `"portrait"`). |
| `scale_name` | `str` | Name of the Plone image scale (for example, `"preview"`, `"thumb"`, `"large"`). |

### Return value

Return a 4-tuple `(left, top, right, bottom)` of integer pixel coordinates,
or `None` if no crop should be applied for this field/scale combination.

The coordinates define a bounding box on the *original* (unscaled) image.
Thumbor crops the source image to this box before resizing to the target
dimensions.

## Register the adapter

Create a ZCML file to register your adapter:

```xml
<configure xmlns="http://namespaces.zope.org/zope">

  <adapter
      factory=".cropping.MyCropProvider"
      provides="plone.pgthumbor.interfaces.ICropProvider"
      for="*"
      />

</configure>
```

The `for="*"` registration makes the adapter available for all content
types.
You can restrict it to specific interfaces if needed:

```xml
<adapter
    factory=".cropping.MyCropProvider"
    provides="plone.pgthumbor.interfaces.ICropProvider"
    for="my.package.interfaces.IMyContentType"
    />
```

## Conditional registration

If your crop provider depends on a third-party package, use ZCML
conditions to register it only when that package is installed:

```xml
<configure
    xmlns="http://namespaces.zope.org/zope"
    xmlns:zcml="http://namespaces.zope.org/zcml">

  <adapter
      zcml:condition="installed some.addon"
      factory=".cropping.MyCropProvider"
      provides="plone.pgthumbor.interfaces.ICropProvider"
      for="*"
      />

</configure>
```

## How crops affect URL generation

When `get_crop()` returns coordinates:

- The crop box is included in the Thumbor URL as `{left}x{top}:{right}x{bottom}`.
- `fit_in` is forced to `True` (the cropped region is fit into the target dimensions).
- `smart` cropping is forced to `False` (explicit crop overrides smart detection).

When `get_crop()` returns `None`, Thumbor URL generation proceeds as
usual based on the scale mode and smart cropping settings.

## Adapter lookup order

Only one `ICropProvider` adapter is active at a time.
The ZCA adapter
registry uses standard precedence rules: a more specific `for` interface
wins over `for="*"`.
If you need to override the built-in
`plone.app.imagecropping` adapter, register yours for a more specific
interface or use `overrides.zcml`.
