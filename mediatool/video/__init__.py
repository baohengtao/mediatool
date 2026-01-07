
from typer import Typer

from . import concat, transform, trim

app = Typer()
for app_ in [concat.app, transform.app, trim.app]:
    app.add_typer(app_)
