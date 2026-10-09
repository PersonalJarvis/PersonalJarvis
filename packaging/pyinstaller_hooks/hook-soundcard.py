"""SoundCard loads its native API declarations from bundled C header files."""

from PyInstaller.utils.hooks import collect_data_files

datas = collect_data_files("soundcard", includes=["*.h"])
