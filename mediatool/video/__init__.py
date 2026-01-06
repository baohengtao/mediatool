
from typer import Typer

from . import concat, cover, transform, trim

app = Typer()
for app_ in [cover.app, transform.app, concat.app, trim.app]:
    app.registered_commands += app_.registered_commands
