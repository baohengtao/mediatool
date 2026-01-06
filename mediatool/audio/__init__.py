
from typer import Typer

from . import mp3

app = Typer()
for app_ in [mp3.app]:
    app.registered_commands += app_.registered_commands
