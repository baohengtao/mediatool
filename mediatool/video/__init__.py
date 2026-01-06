
from typer import Typer

from . import cover, transform

app = Typer()
for app_ in [cover.app, transform.app]:
    app.registered_commands += app_.registered_commands
