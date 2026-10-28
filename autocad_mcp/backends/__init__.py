from .com_backend import ComBackend
from .ezdxf_backend import EzdxfBackend

BACKENDS = {"com": ComBackend(), "ezdxf": EzdxfBackend()}
