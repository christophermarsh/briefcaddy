"""Reuse the unit restricted world without e2e module-name/path collisions."""
import importlib.util
import sys
from pathlib import Path

_name = "assignment_restricted_fixture_source"
_path = Path(__file__).with_name("test_restricted.py")
_source = sys.modules.get(_name)
if _source is None:
    _spec = importlib.util.spec_from_file_location(_name, _path)
    _source = importlib.util.module_from_spec(_spec)
    sys.modules[_name] = _source
    _spec.loader.exec_module(_source)

world = _source.world
app = _source.app
server = _source.server
call = _source.call
sign_in = _source.sign_in
PASSWORD = _source.PASSWORD
