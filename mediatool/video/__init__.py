
from typer import Typer

from . import cover

app = Typer()
for app_ in [cover.app]:
    app.registered_commands += app_.registered_commands
