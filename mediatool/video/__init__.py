
from typer import Typer

from . import cover, editor, transform, trim

app = Typer()
for app_ in [cover.app, transform.app, editor.app, trim.app]:
    app.registered_commands += app_.registered_commands
