import pickle
import uuid

import cloudpickle

from .config import logger
from .object import Object

PICKLE_PROTOCOL = 4  # Support older Python versions.


class Pickler(cloudpickle.Pickler):
    def __init__(self, session, buf):
        self.session = session
        super().__init__(buf, protocol=PICKLE_PROTOCOL)

    def persistent_id(self, obj):
        if not isinstance(obj, Object):
            return
        type_prefix = obj._type_prefix
        if not obj.object_id:
            raise Exception(f"Can't serialize object {obj} which hasn't been created")
        return (type_prefix, obj.object_id)


class Unpickler(pickle.Unpickler):
    def __init__(self, session, prefix_to_type, buf):
        self.session = session
        self.prefix_to_type = prefix_to_type
        super().__init__(buf)

    def persistent_load(self, pid):
        type_prefix, object_id = pid
        if type_prefix not in self.prefix_to_type:
            raise Exception(f"Unknown prefix {type_prefix}")
        cls = self.prefix_to_type[type_prefix]
        obj = Object.__new__(cls)
        obj._init_attributes()
        obj.set_object_id(object_id, self.session)
        return obj
