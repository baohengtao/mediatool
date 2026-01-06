
from typer import Typer

from . import mp3, repair

app = Typer()
for app_ in [mp3.app, repair.app]:
    app.registered_commands += app_.registered_commands
