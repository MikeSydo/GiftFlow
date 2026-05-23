from django.apps import AppConfig


class GiftsConfig(AppConfig):
    name = 'gifts'

    def ready(self):
        import gifts.signals
