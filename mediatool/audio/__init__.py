
from typer import Typer

from . import loudnorm, mp3, repair

app = Typer()
for app_ in [loudnorm.app, repair.app, mp3.app]:
    app.add_typer(app_)
