import functools
import inspect

import synchronicity

from modal_utils.async_utils import synchronize_apis, synchronizer

from ._app_singleton import get_container_app
from .object import Object


def _create_callback(fun):
    # This is a bit of an ugly hack, but we need to know what interface the
    # user function will use to return objects (eg it might return a sync
    # version of some object, and we want to convert it to an internal type).
    # We infer it from the function signature.
    if inspect.iscoroutinefunction(fun):
        interface = synchronicity.Interface.ASYNC
    elif inspect.isfunction(fun):
        interface = synchronicity.Interface.BLOCKING
    else:
        raise Exception(f"{fun}: expected function but got {type(fun)}")

    # Create a coroutine we can use internally
    return synchronizer.create_callback(fun, interface)


def _get_tag(f):
    return f.__qualname__


def _local_construction_make(app, cls, fun):
    callback = _create_callback(fun)

    class _UserFactory(cls):  # type: ignore
        """Acts as a wrapper for a transient Object.

        Conceptually a factory "steals" the object id from the
        underlying object at construction time.
        """

        def __init__(self):
            # This is the only place where tags are being set on objects,
            # besides Function
            Object.__init__(self, app)
            tag = _get_tag(fun)
            app._register_object(tag, self)

        async def load(self, app, existing_object_id):
            if get_container_app() is not None:
                assert False
            obj = await callback()
            if not isinstance(obj, cls):
                raise TypeError(f"expected {obj} to have type {cls}")
            # Then let's create the object
            object_id = await obj.load(app, existing_object_id)
            # Note that we can "steal" the object id from the other object
            # and set it on this object. This is a general trick we can do
            # to other objects too.
            return object_id

    synchronize_apis(_UserFactory)
    return _UserFactory()


def _local_construction(app, cls):
    """Used as a decorator."""
    return functools.partial(_local_construction_make, app, cls)
