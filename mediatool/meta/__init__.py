from typer import Typer

from . import chapter, cover, info

app = Typer()
for app_ in [chapter.app, cover.app, info.app]:
    app.add_typer(app_)
