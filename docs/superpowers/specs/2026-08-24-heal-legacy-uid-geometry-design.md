# Legacy uid healing: recover the scale, not *a* scale — Design

**Date:** 2026-08-24
**Status:** approved
**Driver:** issue #21 — `_heal_legacy_uid` loses mode, collides on width, and mishandles width-0 scales
**Blocks:** `docs/superpowers/specs/2026-08-21-thumbor-source-derivative-design.md` (§5, §9 step 1)

## Problem

The uid healing added for #17 recovers *a* scale, not *the* scale. A healed uid renders
with different geometry than the uid it replaces, silently: the image loads, it is simply
the wrong shape.

Issue #21 names three defects in `ThumborScaleStorage._heal_legacy_uid` and
`_allowed_scale_sizes`. A pre-implementation review against the installed sources
(`plone.scale` 5.0.0a3, `plone.namedfile` 7.3.0, sdist of 8.0.0a3, and Pillow itself)
confirms all three, corrects the fix the issue proposes, and finds two further defects
without which fixing #21 has no observable effect.

## Review of the issue as filed

### Confirmed

**Defect 2 — `_allowed_scale_sizes()` collides on width.** `sizes.setdefault(int(width),
…)` in `storage.py` keeps the first entry per width; a later scale sharing that width
becomes unreachable. Real on the motivating deployment: `Haeuser 400:200` heals as
`400:0` behind `preview 400:0`, `Slider 1000:500` heals as `1000:1000` behind
`large 1000:1000`.

**Defect 3 — width-0 scales heal to the original dimensions.** `if width == 0: dims =
(None, None)` reads a `0:H` uid as "original size". Besides the wrong geometry, this is
the one variant that can push Thumbor past `MAX_PIXELS` and turn a healed uid into an
HTTP 400.

The issue's parenthetical about `hash_key` is also right, and matters more than it
suggests — see §3.

### Corrected: the proposed fix cannot work as described

The issue states: *"At healing time `self.modified_time` is known."* It is not.
`plone.namedfile`'s `ImageScaling.publishTraverse` builds the storage for the uid branch
as `getMultiAdapter((self.context, None), IImageScaleStorage)` — identical in 7.3.0 and
8.0.0a3 — so `modified` is `None`. Minting used
`functools.partial(ImageScaling.modified, fieldname)`. Verified:

```text
modified_time at healing time: None
mint uid: image-400-b2a8f85efaa9013c8c3b6b1da4048e50
heal uid: image-400-de50ed6785f2a914ebb7f0c824fb45c6
equal?  : False
```

Candidate hashing therefore matches nothing until the mint time is reconstructed. This is
the central element of the fix, not a detail. The same wrong assumption appears in §5 of
the derivative design and needs correcting there.

### Corrected: defect 1 is a symptom of a wider defect

`plone.scale`'s `pre_scale` builds its info dict as `uid, key, modified, mimetype, data,
width, height`. **`mode` is not in it** — it lives only inside `info["key"]`, the hashed
parameter tuple. `ImageScaling.scale()` adds `srcset` and `fieldname` and nothing else.

All four call sites in `src/plone/pgthumbor/scaling.py` — lines 195, 215, 253 and 284 —
read `info.get("mode", "scale")`.
The key is never present, so **every Thumbor URL is built with `mode="scale"`**,
healed or not. `_get_crop` already does the right thing and reads the scale name out of
`info["key"]`.

Two consequences:

1. Defect 1 as filed — *"a uid minted for a `cover` scale heals into a letterboxed
   fit-in"* — describes the normal state of every `cover` scale, not a healing
   regression.
2. **Recovering `mode` during healing is inert** while the plumbing gap exists;
   `_build_thumbor_url` discards it one call later. The plumbing fix is a necessary
   condition for defect 1, not an adjacent concern.

### New: `scale_mode_to_thumbor` maps `cover` and `contain` the wrong way round

Measured against real Pillow output (`scalePILImage`, target box 400×200):

| Original | `scale` | `contain` | `cover` |
| --- | --- | --- | --- |
| 1000×400 | 400×160 | **400×200** | 400×160 |
| 400×1000 | 80×200 | **400×200** | 80×200 |
| 200×100 | 200×100 | 200×100 | **400×200** |

`contain` fills the box exactly and crops — that is Thumbor's default, `fit_in=False`.
`cover` never crops; it fits inside and may scale up — that is `fit_in=True`.
`url.py`'s mapping is inverted. The confusion is understandable: `plone.scale`'s own
aliases (`scale-crop-to-fill` for `cover`) and its docstring's claim to follow CSS
`background-size` both describe the opposite of what `_calculate_all_dimensions`
implements.

This is forced into scope: the plumbing fix above makes `mode` effective for the first
time, and with the inverted table `contain` would become accidentally correct while
`cover` became newly wrong.

It also supplies a third and more likely route to `bda/aaf/deployment#5` ("Slider uses
fit-in scales → jumping image sizes") than the healing hypothesis in §5 of the derivative
design: `pre_scale` computes the tag's `width`/`height` from the *real* mode, while the
URL is built as `fit_in` — so the delivered image has different dimensions than the
`<img>` element claims, on every render, with no healing involved. Stated as a
hypothesis to verify against the deployment's templates, not a diagnosis.

## Goals

1. A healed uid renders with the geometry the uid was minted for — dimensions and mode.
2. The mode a caller asked for reaches Thumbor, on every path.
3. A uid that cannot be identified degrades to a defined, documented, safe fallback —
   never to a request at the original's dimensions unless that is the only possible
   reading.

## Non-goals

- HiDPI (`quality`) uids — see Accepted limitations.
- Closing the gap where `srcset`, HiDPI and `image_scales` never forward a scale name, so
  `_get_crop` finds nothing and only `src` is ever cropped. Pre-existing; the derivative
  design already records it as an expectation rather than a regression.
- Issues #14 and #15 (`image_scales` metadata contents).
- Any change to the derivative design. This ships first and independently.

## Design

### 1. Defects and their fixes

| # | Defect | Fix |
| --- | --- | --- |
| A | Traversal builds the storage with `modified=None`, so no hash comparison can match | Reconstruct the mint time, set `self.modified` |
| B | `mode` lives only in `info["key"]`; all four call sites see `"scale"` | `_scale_param()` reads from `key`, normalised through `get_scale_mode` |
| C | `cover`/`contain` → `fit_in` inverted | Correct the mapping, with the evidence in a comment |
| 1 | `mode` hardcoded in healing | Dissolves — the mode is recovered with the rest |
| 2 | `_allowed_scale_sizes()` collides on width | Registry order as a list of `(name, width, height)`, no deduplication |
| 3 | `0:H` heals to original dimensions | Dissolves — `(0, 460)` is a candidate like any other |

### 2. Module layout

A new module `src/plone/pgthumbor/uid_healing.py` holds pure functions with no ZODB and
no storage instance:

- `parse_legacy_uid(uid) -> (fieldname, dimension) | None` — the existing regex
- `registered_scales() -> tuple[tuple[str, int, int], ...]` — a tolerant registry parser
  in registry order, replacing `_allowed_scale_sizes()`
- `candidate_parameters(fieldname, dimension, scales, original_size) -> Iterator[dict]`

`storage.py` keeps only the glue: reconstruct the mint time, hash the candidates, fall
back, call `pre_scale`. The awkward part — the enumeration — becomes testable without
mocks.

### 3. Candidate enumeration

`hash_key` deletes the `scale` key **only** when width *and* height are truthy. The
consequence is that one registered scale can correspond to more than one uid, and which
ones depends on its dimensions. Verified against `plone.scale` 5.0.0a3:

| Registered scale | `tag(scale=…)` | explicit dims (`image_scales`) | no `scale` key (`srcset`) |
| --- | --- | --- | --- |
| `Haeuser 400:200` | one uid, shared by all three | | |
| `preview 400:0` | own uid | own uid | own uid |
| `Header 0:460` | own uid | own uid | own uid |

Missing this splits the difference exactly where it hurts: the `0:H` scales of defect 3
would still fail to heal when the uid came from cached HTML.

For every `(name, w, h)` with `w == dimension`, crossed with the modes:

- `w and h` → one shape: `{fieldname, width, height, mode, scale: None}`
- otherwise → three shapes: `scale: None`, `scale: name`, and no `scale` key at all
- additionally when `dimension == 0`: `width=None, height=None` (a genuine `tag()` with
  no width), in the `scale: None` and no-`scale`-key shapes — such a call carries no
  scale name
- additionally when `original_size[0] == dimension`: the original's dimensions — the
  `download` entry minted by `ImageFieldScales.get_original_image_url`

`original_size` is the field value's `getImageSize()`. When the field is empty or the
value is unreadable it is `None` and that last candidate is skipped; every other
candidate is unaffected.

Modes: the three canonical ones plus every alias `get_scale_mode` accepts (`keep`,
`thumbnail`, `down`, `up`, `scale-crop-to-fit`, `scale-crop-to-fill`, `None`).
`hash_key` hashes the *raw* string a caller passed, not the normalised one, so an alias
that is not enumerated is a silent miss. Cost: with ~15 registered scales, typically
10–90 md5 computations per volatile miss, once.

Enumeration order is registry order, then the canonical mode order. Order is only
observable through the fallback (§4), never through a match — but it must be
deterministic.

### 4. Matching, mint time, and fallback

```python
mint_time = ImageScaling(self.context, getRequest()).modified(fieldname)
self.modified = lambda: mint_time
for parameters in candidates:
    if self.hash_key(**parameters) == uid:
        return self.pre_scale(**parameters)
```

`ImageScaling` is a `BrowserView` whose `__init__` only assigns, so instantiating it
directly is a constructor call — no component lookup, no ZCML dependency, and no copy of
`modified()` to drift out of sync. It is byte-identical between 7.3.0 and 8.0.0a3.
Verified: returns the field's `modified` when present, falls back to the context's
`_p_mtime` when not.

Setting `self.modified` has a second benefit. With the correct mint time, the uid
`pre_scale` returns **equals the requested uid**, so `IStableImageScale` and the
`@@images/{uid}` fallback URL stay coherent — which they are not today.

**No candidate matches** — the uid predates the image's last modification. The image is
served, not 404'd: `plone.scale` deliberately returns outdated scales at this point, and
a cached page whose image has been replaced should show the current image, not a hole.
The fallback is the first registered scale whose width matches, with `mode="scale"`.
Original dimensions are requested only when `dimension == 0` **and** no `0:H` scale is
registered — the genuine no-width `tag()` case, and the only reading left. When no
registered scale matches the width at all, the result stays `None` and traversal raises
`NotFound`, as today: an unregistered width is exactly the case #17's gate was built to
reject, and nothing here weakens it.

That last rule is what keeps this design independent of the derivative one. §5 there
notes that a derivative plus the §4 clamp turns a request at original dimensions from a
loud HTTP 400 into a silent wrong-size render; a fallback that never makes such a request
gratuitously cannot inherit that regression. The dependency runs the other way too: §1
there notes that writing a derivative onto a legacy field value without `_modified` moves
its `_p_mtime`, which moves the mint time, which means **no candidate matches for that
entire population**. Every render of theirs from cached HTML lands in this fallback. It
is the backfill's safety net.

The fallback is a separate method with its own docstring and its own tests — a deliberate
branch, not a side effect.

### 5. Mode plumbing in `scaling.py`

A helper `_scale_param(info, name, default)` consults `dict(info["key"])` first and the
info dict second. `_get_crop` moves onto it — it already does this by hand for the scale
name. All four `_build_thumbor_url` calls pass
`get_scale_mode(_scale_param(info, "mode", "scale"))`; normalising first means the aliases
collapse before `scale_mode_to_thumbor` sees them, so that function keeps its three-way
shape.

Both `plone.namedfile` paths are affected and both are covered: the 7.x
`ThumborImageScale.__init__` path and the 8.x `_scale_url` path, plus
`srcset_attribute` and `ThumborImageScaling._scale_url`.

**Upstream, and why this is still the right shim.** `plone/plone.scale#156` (open since
2026-08-23) adds `mode`, `scale` and `fieldname` to the info dict, closing this gap at
the source and naming pgthumbor as its motivation. It is not merged, and a merge still
needs a `plone.scale` release and a Plone pin, so it cannot be depended on. It does not
need to be: reading `info["key"]` first and the info dict second works unchanged before
and after that PR, and the helper can be dropped once the floor moves. Two details make
key-first the safer order — `key` is present in every version, and the PR stores the
*normalised* mode in `pre_scale` but the *raw* one in `generate_scale`, while `key`
consistently holds the raw value that `hash_key` also hashed. Normalising through
`get_scale_mode` makes all three agree.

The PR does not touch uid computation, so minted uids stay valid and §3's enumeration is
unaffected by it either way.

### 6. The `cover`/`contain` correction

`scale_mode_to_thumbor` becomes:

| Plone mode | Thumbor | Why |
| --- | --- | --- |
| `scale` | `fit_in=True` | fits inside the box, never scales up |
| `cover` | `fit_in=True` | fits inside the box, may scale up — never crops |
| `contain` | `fit_in=False` | fills the box exactly by cropping |

The code carries a comment stating that this contradicts `plone.scale`'s own alias names
and its CSS `background-size` docstring, and that the table above was measured against
`scalePILImage` rather than read off those names. Without that note the next reader will
"fix" it back.

`smart` follows the `smart_cropping` setting on all three modes. It is inert under
`fit_in`, so the only mode where it now takes effect is `contain` — which is the mode
that crops, and therefore the one smart cropping exists for. Today it is hardcoded off
there and enabled on the two modes where Thumbor ignores it.

## Testing

Test-driven, with mint-then-heal as the basic figure: a real storage instance with a
stubbed `modified` callable mints a uid through `hash_key(**parameters)`, and
`_heal_legacy_uid` must return **exactly those** parameters. This covers all three issue
defects in one shape and cannot pass by accident.

- `cover` uid → recovers `mode="cover"`, not `"scale"`
- `Haeuser 400:200` registered after `preview 400:0` → recovers `(400, 200)`
- `Header 0:460` → recovers `(0, 460)`, not `(None, None)`
- all three `scale`-key shapes for `400:0` and `0:460`
- fallback: a uid with a stale mint time → the deterministic scale; never original
  dimensions while a `0:H` scale is registered; original dimensions when
  `dimension == 0` and none is
- regressions on `parse_legacy_uid`: dashes in the fieldname, a 5000-digit width,
  non-hex tails

For `scaling.py`: an info dict whose `key` tuple carries `mode="contain"` produces
`fit_in=False`, and `cover` produces `fit_in=True` — on both namedfile paths and in
`srcset_attribute`.

For the mode mapping: a table-driven test that scales a real `PIL.Image` through
`scalePILImage` for each mode and asserts that Thumbor's `fit_in` choice matches whether
`plone.scale` cropped. That keeps the evidence executable rather than asserting the
mapping against itself.

Existing tests that encode the defects must be inverted, not deleted quietly:
`test_first_width_wins_on_duplicates` (defect 2) and
`test_get_or_generate_width_zero_means_original` (defect 3). `TestAllowedScaleSizes`
changes wholesale with the return type.

## Accepted limitations

- **HiDPI (`quality`) uids are not enumerated.** `calculate_srcset` mints uids at
  `width × factor` with a `quality` parameter in the hash. Those widths are not
  registered sizes, so they are already rejected by the width gate; enumerating them
  would mean guessing quality values as well. `plone.highpixeldensity_scales` is off by
  default, and under pgthumbor `srcset_attribute` emits Thumbor URLs rather than uids.
  Such a uid heals through the fallback or 404s, as today.
- **A uid older than the image's last modification cannot be identified**, only guessed
  at (§4). This is inherent: `modified` is part of the hash.
- **`contain` maps to Thumbor's plain crop-to-fill**, which centres the crop.
  `plone.scale` also centres. Sub-pixel rounding differences between the two remain
  possible and are out of scope.

## Rollout

One PR against `main` in `sources/plone-pgthumbor`, branch
`fix/21-heal-legacy-uid-geometry`, with a CHANGES entry for 0.6.6.

Afterwards, and before the derivative work resumes, §5 of
`2026-08-21-thumbor-source-derivative-design.md` needs two corrections: the
`self.modified_time` assumption in its #21 paragraph, and the aaf#5 hypothesis, which now
has a third and more probable route that does not involve healing at all.

Then release, then the derivative design's §9 continues from its step 2.
