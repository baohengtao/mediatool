from typer import Typer

from . import chapter, cover, info, subtitle

app = Typer()
for app_ in [chapter.app, cover.app, subtitle.app, info.app]:
    app.registered_commands += app_.registered_commands
