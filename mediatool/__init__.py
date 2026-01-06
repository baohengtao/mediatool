"""
Edit video using ffmpeg
"""
from rich.console import Console
from rich.theme import Theme
from rich.traceback import install

__version__ = '2025.1.6'


custom_theme = Theme({
    "info": "dim cyan",
    "warning": "bold bright_yellow on dark_orange",
    "error": "bold bright_red on dark_red",
    "notice": "bold magenta"
})
console = Console(theme=custom_theme, record=True, width=126)
install(show_locals=False)
