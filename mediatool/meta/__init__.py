
from typer import Typer

from . import chapter, cover

app = Typer()
for app_ in [chapter.app, cover.app]:
    app.registered_commands += app_.registered_commands
