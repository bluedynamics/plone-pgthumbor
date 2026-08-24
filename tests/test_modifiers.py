"""Keeping Thumbor source derivatives out of CMFEditions snapshots.

``Products.CMFEditions`` snapshots content by deep-pickling it.
``plone.app.versioningbehavior``'s ``CloneNamedFileBlobs`` protects blobs
from that pickle by collecting them — but it walks **top-level field values
only**, returning ``field_value._blob`` per ``INamedBlobImageField``.  A
nested ``_pgthumbor_source._blob`` is not in that mapping, so it goes
through the pickle, and ``ZODB.blob.Blob.__getstate__`` returns ``None``.

The result is not an error.  It is a ``NamedBlobImage`` that looks entirely
valid and reads back zero bytes, which is the worse outcome: after a
revert, ``get_blob_ids`` resolves it to a real ``(zoid, tid)``, Thumbor
fetches nothing, and the structural-invalidation argument does not save us
because the attribute *is* present.
"""

from __future__ import annotations

from pickle import Pickler
from pickle import Unpickler
from tests.conftest import jpeg_bytes
from tests.conftest import namedfile_storables
from tests.conftest import zodb_db

import io
import pytest
import transaction


class _Content:
    """A stand-in content object.  Plain, so it pickles by value."""

    def __init__(self, **fields):
        self.__dict__.update(fields)


def _named_image(data=None, filename="t.jpg"):
    from plone.namedfile.file import NamedBlobImage

    return NamedBlobImage(
        data=jpeg_bytes() if data is None else data,
        filename=filename,
        contentType="image/jpeg",
    )


def _content_with_derivative(connection, info=None):
    """A committed content object whose image carries a derivative."""
    from plone.pgthumbor.derivative import INFO_ATTRIBUTE
    from plone.pgthumbor.derivative import SOURCE_ATTRIBUTE

    image = _named_image()
    setattr(image, SOURCE_ATTRIBUTE, _named_image(filename="d.jpg"))
    setattr(
        image,
        INFO_ATTRIBUTE,
        info or {"reason": "generated", "max_edge": 4000, "source_ids": None},
    )
    content = _Content(image=image)
    connection.root()["content"] = content
    transaction.commit()
    return content


def _clone_by_pickle(obj, callbacks=None):
    """What ``CMFEditions._cloneByPickle`` does, in miniature."""
    stream = io.BytesIO()
    pickler = Pickler(stream, 1)
    if callbacks is not None:
        pickler.persistent_id = callbacks[0]
    pickler.dump(obj)
    stream.seek(0)
    unpickler = Unpickler(stream)
    if callbacks is not None:
        unpickler.persistent_load = callbacks[1]
    return unpickler.load()


def _pin_schema(monkeypatch, schema):
    from plone.pgthumbor import subscribers

    monkeypatch.setattr(subscribers, "iterSchemata", lambda obj: [schema])


def _image_schema():
    from plone.namedfile.field import NamedBlobImage as NamedBlobImageField
    from zope.interface import Interface
    from zope.schema import TextLine

    class IFakeSchema(Interface):
        image = NamedBlobImageField(title="Image", required=False)
        title = TextLine(title="Title", required=False)

    return IFakeSchema


class TestTheProblem:
    """Why this modifier has to exist at all."""

    def test_a_nested_derivative_pickles_to_an_empty_image(self, monkeypatch):
        with namedfile_storables(), zodb_db() as db:
            content = _content_with_derivative(db.open())

            assert len(content.image._pgthumbor_source.data) > 0

            clone = _clone_by_pickle(content)

            # Not an exception.  A NamedBlobImage that looks fine and holds
            # nothing — which after a revert becomes a valid (zoid, tid)
            # naming an empty blob, and a Thumbor 400 with no clue why.
            assert clone.image._pgthumbor_source is not None
            assert len(clone.image._pgthumbor_source.data) == 0


class TestOnCloneModifiers:
    """The callbacks the modifier hands CMFEditions."""

    def _modifier(self):
        from plone.pgthumbor.modifiers import SkipThumborSourceDerivatives

        return SkipThumborSourceDerivatives("test", "Test")

    def test_the_derivative_does_not_reach_the_clone(self, monkeypatch):
        _pin_schema(monkeypatch, _image_schema())
        with namedfile_storables(), zodb_db() as db:
            content = _content_with_derivative(db.open())
            callbacks = self._modifier().getOnCloneModifiers(content)

            clone = _clone_by_pickle(content, callbacks)

            assert clone.image._pgthumbor_source is None

    def test_the_outcome_record_does_not_reach_the_clone_either(self, monkeypatch):
        """Dropping only the derivative would be worse than dropping neither.

        A reverted object would then carry a terminal outcome record and no
        derivative, ``needs_processing`` would answer False, and nothing
        would ever regenerate it — the original would be served to Thumbor
        forever, silently.
        """
        from plone.pgthumbor.derivative import needs_processing

        _pin_schema(monkeypatch, _image_schema())
        with namedfile_storables(), zodb_db() as db:
            content = _content_with_derivative(db.open())
            callbacks = self._modifier().getOnCloneModifiers(content)

            clone = _clone_by_pickle(content, callbacks)

            assert clone.image._pgthumbor_source_info is None
            assert needs_processing(clone.image, 4000) is True

    def test_the_original_blob_is_untouched(self, monkeypatch):
        _pin_schema(monkeypatch, _image_schema())
        with namedfile_storables(), zodb_db() as db:
            content = _content_with_derivative(db.open())
            callbacks = self._modifier().getOnCloneModifiers(content)

            _clone_by_pickle(content, callbacks)

            # The source object must come out of a clone exactly as it went
            # in; the modifier only chooses what the *pickle* sees.
            assert len(content.image._pgthumbor_source.data) > 0

    def test_it_composes_with_clone_named_file_blobs(self, monkeypatch):
        """Both modifiers active at once, the way portal_modifier runs them.

        ``ModifierRegistryTool.getOnCloneModifiers`` chains every registered
        ICloneModifier and prefixes each pid with the modifier's id, so ours
        adds to CloneNamedFileBlobs rather than replacing it: the top-level
        blob still survives by reference, the nested one still goes away.
        """
        from plone.app.versioningbehavior.modifiers import getCallbacks

        _pin_schema(monkeypatch, _image_schema())
        with namedfile_storables(), zodb_db() as db:
            content = _content_with_derivative(db.open())
            ours = self._modifier().getOnCloneModifiers(content)
            theirs = getCallbacks([content.image._blob])

            def persistent_id(obj):
                for index, callback in enumerate((ours[0], theirs[0])):
                    found = callback(obj)
                    if found is not None:
                        return f"{index}/{found}"

            def persistent_load(named):
                return None

            clone = _clone_by_pickle(content, (persistent_id, persistent_load))

            assert clone.image._pgthumbor_source is None
            # CloneNamedFileBlobs reattaches this afterwards; what matters
            # is that it was replaced rather than deep-copied empty.
            assert clone.image._blob is None

    def test_nothing_to_do_returns_none(self, monkeypatch):
        _pin_schema(monkeypatch, _image_schema())
        with namedfile_storables():
            content = _Content(image=_named_image())

            # No callbacks at all is the documented "nothing to do" answer,
            # and it lets CMFEditions skip the persistent_id hook entirely.
            assert self._modifier().getOnCloneModifiers(content) is None

    def test_an_object_without_image_fields_returns_none(self, monkeypatch):
        _pin_schema(monkeypatch, _image_schema())

        assert self._modifier().getOnCloneModifiers(_Content()) is None


class TestInstallation:
    """Registering into portal_modifier, and surviving its absence."""

    def _site(self, tool=None):
        from unittest.mock import MagicMock

        site = MagicMock()
        site.portal_modifier = tool
        return site

    def test_registers_the_modifier(self, monkeypatch):
        from plone.pgthumbor import setuphandlers
        from unittest.mock import MagicMock

        tool = MagicMock()
        tool.objectIds.return_value = []
        monkeypatch.setattr(
            setuphandlers, "getToolByName", lambda site, name, default=None: tool
        )

        assert setuphandlers.install_clone_modifier(self._site(tool)) is True
        assert tool.register.call_count == 1
        assert tool.register.call_args[0][0] == setuphandlers.MODIFIER_ID

    def test_registration_is_idempotent(self, monkeypatch):
        from plone.pgthumbor import setuphandlers
        from unittest.mock import MagicMock

        tool = MagicMock()
        tool.objectIds.return_value = [setuphandlers.MODIFIER_ID]
        monkeypatch.setattr(
            setuphandlers, "getToolByName", lambda site, name, default=None: tool
        )

        # Upgrade steps get re-run, and a second register() would raise on
        # the duplicate id.
        assert setuphandlers.install_clone_modifier(self._site(tool)) is False
        assert tool.register.call_count == 0

    def test_a_missing_portal_modifier_is_not_an_error(self, monkeypatch, caplog):
        from plone.pgthumbor import setuphandlers

        # portal_modifier exists only once the CMFEditions GenericSetup
        # profile has been applied, and plone.app.versioningbehavior reaches
        # a site through plone.app.contenttypes rather than Plone core.  A
        # site with custom types and neither has no repository at all, so
        # there are no snapshots for this modifier to protect.
        monkeypatch.setattr(
            setuphandlers, "getToolByName", lambda site, name, default=None: default
        )

        with caplog.at_level("INFO"):
            assert setuphandlers.install_clone_modifier(self._site()) is False

    def test_a_missing_cmfeditions_package_is_not_an_error(self, monkeypatch):
        from plone.pgthumbor import setuphandlers

        import builtins

        real_import = builtins.__import__

        def no_cmfeditions(name, *args, **kwargs):
            if "CMFEditions" in name or name.endswith("modifiers"):
                raise ImportError(f"no {name}")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", no_cmfeditions)

        assert setuphandlers.install_clone_modifier(self._site()) is False


class TestProfileWiringForTheModifier:
    """The step that gets it onto a site that is already installed."""

    def _package_dir(self):
        import pathlib
        import plone.pgthumbor

        return pathlib.Path(plone.pgthumbor.__file__).parent

    def test_upgrade_step_4_to_5_is_registered(self):
        from xml.etree import ElementTree

        zcml = ElementTree.parse(self._package_dir() / "configure.zcml").getroot()
        steps = [
            element
            for element in zcml.iter()
            if element.tag.endswith("upgradeStep")
            and element.get("source") == "4"
            and element.get("destination") == "5"
        ]

        assert len(steps) == 1
        assert steps[0].get("handler") == ".setuphandlers.upgrade_to_5"

    def test_upgrade_to_5_installs_the_modifier(self, monkeypatch):
        from plone.pgthumbor import setuphandlers

        calls = []
        monkeypatch.setattr(
            setuphandlers, "install_clone_modifier", lambda site: calls.append(site)
        )
        setuphandlers.upgrade_to_5(object())

        # post_install runs on install only, so without this an already
        # installed site — the one this design exists to repair — would take
        # the new code and never receive the modifier.
        assert len(calls) == 1

    def test_post_install_also_installs_the_modifier(self, monkeypatch):
        from plone.pgthumbor import setuphandlers
        from unittest.mock import MagicMock

        calls = []
        monkeypatch.setattr(setuphandlers, "getUtility", lambda iface: MagicMock())
        monkeypatch.setattr(
            setuphandlers, "install_clone_modifier", lambda site: calls.append(site)
        )
        setuphandlers.post_install(MagicMock())

        assert len(calls) == 1


def test_plone_app_iterate_needs_no_hook():
    """Documented, not implemented — and the reason is worth keeping.

    ``plone.app.iterate`` checkout copies the object through the same
    CMFEditions clone machinery, so the modifier registered above already
    covers it.  ``manage_pasteObjects`` goes through
    ``exportFile``/``importFile``, which really does copy blob files, so a
    pasted object carries a working duplicate of the derivative — and it
    re-fires IObjectAddedEvent, which is one of the reasons the subscriber
    has to be idempotent.
    """
    pytest.importorskip("plone.app.iterate", reason="optional add-on")
