from django.contrib.staticfiles.apps import StaticFilesConfig


class CloverStaticFilesConfig(StaticFilesConfig):
    ignore_patterns = [
        *StaticFilesConfig.ignore_patterns,
        'plugins/fontawesome-free-7.1.0-web/*',
    ]
