"""A native-surface sink that records the state and level actually rendered."""


class VoiceSurface:
    def __init__(self):
        self.mode = "idle"
        self.level = 0.0
        self.shown = []

    def show(self, mode="listen"):
        self.mode = mode
        self.shown.append(mode)

    def hide(self):
        self.mode = "hidden"

    def set_level(self, level):
        self.level = level

    def play_animation(self, name):
        pass

    def stop_animation(self, name):
        pass

    def hide_comment(self):
        pass

    def show_comment(self, text, duration_ms=3500):
        pass

    def show_listening_transcript(self, text="", duration_ms=30000):
        pass
